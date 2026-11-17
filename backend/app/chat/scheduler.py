"""Single-process chat scheduling and lazily initialized account state."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.account_logging import logger
from app.accounts.repository import User
from app.chat.memory import MemoryManager
from app.chat.state import AIChatState, create_initial_state
from app.llm.configuration import AdminModelRuntime
from app.llm.personal_models import PersonalModelStore


class ChatNotStarted(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ChatRuntime:
    memorier: MemoryManager
    graph: Any
    state: AIChatState


@dataclass
class AccountChat:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiting: bool = False
    runtime: ChatRuntime | None = None
    worker: asyncio.Task | None = None


class ChatSessions:
    def __init__(self, connection, *, personal_models: PersonalModelStore | None = None,
                 admin_model: AdminModelRuntime | None = None,
                 graph_factory=None, max_running=4, max_waiting=8, wait_timeout=2.0):
        if max_running < 1 or max_waiting < 0 or not wait_timeout > 0:
            raise ValueError('invalid chat capacity')
        self._connection = connection
        self._personal_models = personal_models
        self._admin_model = admin_model
        self._graph_factory = graph_factory
        self._users: dict[int, AccountChat] = {}
        self._slots = asyncio.BoundedSemaphore(max_running)
        self._executor = ThreadPoolExecutor(max_workers=max_running, thread_name_prefix='xiaowan-chat')
        self._max_waiting = max_waiting
        self._wait_timeout = wait_timeout
        self._waiting = 0
        self._closing = False

    @staticmethod
    def _reject(status: int, message: str):
        logger.info('chat admission rejected status=%d', status)
        raise ChatNotStarted(status, message)

    async def send(self, user: User, text: str):
        start = perf_counter()
        user_id = user.id
        if self._closing:
            self._reject(503, '聊天服务正在关闭，请稍后重试')
        entry = self._users.get(user_id)
        if entry is not None and entry.waiting:
            self._reject(429, '这个账号已有一条消息在等待，请稍后重试')
        queued = self._slots.locked() or (entry is not None and entry.lock.locked())
        if queued and self._waiting >= self._max_waiting:
            self._reject(503, '聊天请求过多，请稍后重试')
        if entry is None:
            entry = self._users[user_id] = AccountChat()
        if queued:
            entry.waiting = True
            self._waiting += 1

        account_acquired = False
        slot_acquired = False

        async def acquire():
            nonlocal account_acquired, slot_acquired
            await entry.lock.acquire()
            account_acquired = True
            await self._slots.acquire()
            slot_acquired = True

        try:
            if queued:
                deadline = start + self._wait_timeout
                attempt = asyncio.create_task(acquire())
                try:
                    await asyncio.wait((attempt,), timeout=max(0, deadline - perf_counter()))
                finally:
                    if not attempt.done():
                        attempt.cancel()
                        await asyncio.gather(attempt, return_exceptions=True)
                if attempt.cancelled() or perf_counter() >= deadline:
                    raise asyncio.TimeoutError
                attempt.result()
            else:
                await acquire()
        except BaseException as error:
            if slot_acquired:
                self._slots.release()
            if account_acquired:
                entry.lock.release()
            if isinstance(error, asyncio.TimeoutError):
                self._reject(503, '等待处理超时，请稍后重试')
            raise
        finally:
            if queued:
                entry.waiting = False
                self._waiting -= 1

        if self._closing:
            self._slots.release()
            entry.lock.release()
            self._reject(503, '聊天服务正在关闭，请稍后重试')
        logger.info('chat queue_ms=%.1f', (perf_counter() - start) * 1000)
        work = asyncio.create_task(self._execute(entry, user, text))
        entry.worker = work
        work.add_done_callback(self._consume_error)
        # The worker owns both permits until graph execution and saving finish.
        # Cancelling this HTTP request must not allow another turn to overlap it.
        result = await asyncio.shield(work)
        logger.info('chat response_ready_ms=%.1f', (perf_counter() - start) * 1000)
        return result

    async def drain(self):
        """Finish admitted work before its database connections are closed."""
        self._closing = True
        workers = [entry.worker for entry in self._users.values() if entry.worker is not None]
        await asyncio.gather(*(asyncio.shield(worker) for worker in workers), return_exceptions=True)
        self._executor.shutdown()

    @staticmethod
    def _consume_error(task: asyncio.Task):
        if not task.cancelled():
            task.exception()

    async def _execute(self, entry: AccountChat, user: User, text: str):
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, copy_context().run, self._run, entry, user, text)
        finally:
            entry.worker = None
            self._slots.release()
            entry.lock.release()

    def _snapshot_agents(self):
        snapshot = self._admin_model.snapshot() if self._admin_model is not None else None
        if snapshot is None:
            raise ChatNotStarted(503, '聊天模型尚未就绪，请稍后重试')
        return snapshot[1]

    def _initialize(self, user_id: int) -> ChatRuntime:
        factory = self._graph_factory
        if factory is None:
            from app.chat.workflow import create_graph
            factory = create_graph

        start = perf_counter()
        memorier = MemoryManager(user_id, self._connection)
        graph = factory(memorier)
        state = create_initial_state(memorier.next_round)
        logger.info('chat initialization_ms=%.1f', (perf_counter() - start) * 1000)
        return ChatRuntime(memorier, graph, state)

    def _agents_for(self, user: User):
        if user.role == 'admin':
            return self._snapshot_agents()
        try:
            agents = self._personal_models.agents_for(user.id) if self._personal_models else None
        except ValueError:
            agents = None
        except Exception as error:
            logger.warning('personal model unavailable: %s', type(error).__name__)
            raise ChatNotStarted(503, '个人模型暂时无法加载，请稍后重试') from None
        if agents is None:
            raise ChatNotStarted(403, '请先在设置中配置个人模型和 API Key')
        return agents

    def _run(self, entry: AccountChat, user: User, text: str):
        agents = self._agents_for(user)
        if entry.runtime is None:
            entry.runtime = self._initialize(user.id)
        runtime = entry.runtime
        state = deepcopy(runtime.state)
        state['user_input'] = text
        result = runtime.graph.invoke(state, config={'configurable': {'agents': agents}})
        runtime.state = result
        return result
