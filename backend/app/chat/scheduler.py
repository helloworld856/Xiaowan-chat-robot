"""Single-process chat scheduling and cooperative cancellation."""

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
from threading import Event
from time import perf_counter
from typing import Any

from app.account_logging import discard_logs_after, log_event, logger
from app.accounts.portrait import UserPortraitStore, format_portrait_for_prompt, learn_portrait_from_turn
from app.accounts.repository import User
from app.chat.memory import MemoryManager
from app.chat.progress import ChatCancelled, chat_cancel_context
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
class TurnHandle:
    turn_id: str
    cancel_event: Event
    waiter: asyncio.Task | None = None
    running: bool = False


@dataclass
class AccountChat:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiting: bool = False
    maintenance: bool = False
    runtime: ChatRuntime | None = None
    worker: asyncio.Task | None = None
    learn_task: asyncio.Task | None = None
    cancelling_worker: bool = False
    turns: dict[str, TurnHandle] = field(default_factory=dict)
    cancelled_turns: deque[str] = field(default_factory=lambda: deque(maxlen=16))


class ChatSessions:
    def __init__(self, connection, *, personal_models: PersonalModelStore | None = None,
                 admin_model: AdminModelRuntime | None = None,
                 portraits: UserPortraitStore | None = None,
                 graph_factory=None, max_running=4, max_waiting=8, wait_timeout=2.0):
        if max_running < 1 or max_waiting < 0 or not wait_timeout > 0:
            raise ValueError('invalid chat capacity')
        self._connection = connection
        self._personal_models = personal_models
        self._admin_model = admin_model
        self._portraits = portraits
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

    @asynccontextmanager
    async def maintenance(self, user_id: int):
        if self._closing:
            raise ChatNotStarted(503, '聊天服务正在关闭，请稍后重试')
        entry = self._users.setdefault(user_id, AccountChat())
        if entry.maintenance or entry.waiting or entry.lock.locked():
            raise ChatNotStarted(409, '该账号还有聊天请求正在处理，请稍后重试')
        entry.maintenance = True
        try:
            yield
        finally:
            # 丢弃缓存的历史和摘要，下一轮从数据库重新读取。
            del self._users[user_id]

    def cancel(self, user_id: int, turn_id: str | None = None) -> bool:
        if self._closing:
            return False
        entry = self._users.get(user_id)
        if entry is None:
            if turn_id is None:
                return False
            entry = self._users.setdefault(user_id, AccountChat())
        if turn_id is not None and turn_id not in entry.cancelled_turns:
            entry.cancelled_turns.append(turn_id)

        targets = ([entry.turns[turn_id]] if turn_id in entry.turns else []) if turn_id is not None else list(
            entry.turns.values()
        )
        if not targets:
            return False
        for handle in targets:
            handle.cancel_event.set()
            if handle.running:
                entry.cancelling_worker = True
            if handle.waiter is not None and not handle.waiter.done():
                handle.waiter.cancel()
        logger.info('chat turn cancel_requested count=%d', len(targets))
        return True

    async def send(self, user: User, text: str, *, turn_id: str | None = None):
        return await self._schedule(user, text, regenerate_round=None, turn_id=turn_id)

    async def regenerate(self, user: User, conversation_round: int, *, turn_id: str | None = None):
        return await self._schedule(user, '', regenerate_round=conversation_round, turn_id=turn_id)

    async def _schedule(self, user: User, text: str, *, regenerate_round: int | None, turn_id: str | None):
        start = perf_counter()
        user_id = user.id
        if self._closing:
            self._reject(503, '聊天服务正在关闭，请稍后重试')
        entry = self._users.get(user_id)
        if turn_id and entry is not None and turn_id in entry.cancelled_turns:
            raise ChatCancelled()
        if entry is not None and entry.maintenance:
            self._reject(503, '正在清理账号数据，请稍后重试')
        if entry is not None and entry.waiting:
            self._reject(429, '这个账号已有一条消息在等待，请稍后重试')
        queued = self._slots.locked() or (entry is not None and entry.lock.locked())
        if queued and self._waiting >= self._max_waiting:
            self._reject(503, '聊天请求过多，请稍后重试')
        if entry is None:
            entry = self._users[user_id] = AccountChat()

        handle: TurnHandle | None = None
        if turn_id is not None:
            handle = TurnHandle(turn_id=turn_id, cancel_event=Event())
            entry.turns[turn_id] = handle
            if turn_id in entry.cancelled_turns:
                entry.turns.pop(turn_id, None)
                raise ChatCancelled()

        wait_budget = max(self._wait_timeout, 15.0) if entry.cancelling_worker else self._wait_timeout
        if queued:
            entry.waiting = True
            self._waiting += 1

        account_acquired = False
        slot_acquired = False

        # 先拿账号锁，再占全局执行位，避免同账号的等待任务占住执行容量。
        async def acquire():
            nonlocal account_acquired, slot_acquired
            await entry.lock.acquire()
            account_acquired = True
            await self._slots.acquire()
            slot_acquired = True

        try:
            if queued:
                deadline = start + wait_budget
                attempt = asyncio.create_task(acquire())
                if handle is not None:
                    handle.waiter = attempt
                try:
                    await asyncio.wait((attempt,), timeout=max(0, deadline - perf_counter()))
                finally:
                    if handle is not None:
                        handle.waiter = None
                    if not attempt.done():
                        attempt.cancel()
                        await asyncio.gather(attempt, return_exceptions=True)
                if handle is not None and handle.cancel_event.is_set():
                    raise ChatCancelled()
                if attempt.cancelled() or perf_counter() >= deadline:
                    raise asyncio.TimeoutError
                attempt.result()
            else:
                await acquire()
                if handle is not None and handle.cancel_event.is_set():
                    raise ChatCancelled()
        except BaseException as error:
            if handle is not None:
                entry.turns.pop(handle.turn_id, None)
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
            if handle is not None:
                entry.turns.pop(handle.turn_id, None)
            self._slots.release()
            entry.lock.release()
            self._reject(503, '聊天服务正在关闭，请稍后重试')
        entry.cancelling_worker = False
        if handle is not None:
            handle.running = True
        logger.info('chat queue_ms=%.1f', (perf_counter() - start) * 1000)
        work = asyncio.create_task(self._execute(entry, user, text, regenerate_round=regenerate_round, handle=handle))
        entry.worker = work
        work.add_done_callback(self._consume_error)
        # HTTP 请求断开后，后台任务仍持有账号锁和执行位，直到本轮处理或取消收尾结束。
        result = await asyncio.shield(work)
        logger.info('chat response_ready_ms=%.1f', (perf_counter() - start) * 1000)
        if (regenerate_round is None and isinstance(result, dict) and result.get('saved')
                and self._portraits is not None and not self._closing
                and len(text.strip()) >= 5 and entry.learn_task is None):
            self._schedule_portrait_learning(entry, user, text, list(result.get('ai_response') or ()))
        return result

    def _schedule_portrait_learning(self, entry: AccountChat, user: User, text: str, replies: list[str]) -> None:
        async def run_learn():
            try:
                current = self._portraits.get(user.id)
                if not current.auto_learn:
                    return
                agents = self._agents_for(user)
                _, summary_agent, _ = agents
                loop = asyncio.get_running_loop()
                learned = await loop.run_in_executor(
                    self._executor, copy_context().run,
                    learn_portrait_from_turn, summary_agent, current, text, replies,
                )
                if learned is not None:
                    self._portraits.update_learned(user.id, learned)
                    log_event('portrait_learned', items=len(learned))
            except Exception as error:
                logger.warning('background portrait learn skipped: %s', type(error).__name__)
            finally:
                entry.learn_task = None

        task = asyncio.create_task(run_learn())
        entry.learn_task = task
        task.add_done_callback(self._consume_error)

    async def drain(self):
        """Finish admitted work before its database connections are closed."""
        self._closing = True
        tasks = [
            task for entry in self._users.values()
            for task in (entry.worker, entry.learn_task) if task is not None
        ]
        await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        self._executor.shutdown()

    @staticmethod
    def _consume_error(task: asyncio.Task):
        if not task.cancelled():
            task.exception()

    async def _execute(self, entry: AccountChat, user: User, text: str, *,
                       regenerate_round: int | None = None,
                       handle: TurnHandle | None = None):
        cancel_event = handle.cancel_event if handle is not None else None
        try:
            loop = asyncio.get_running_loop()
            runner = self._run if regenerate_round is None else partial(self._run_regenerate,
                                                                        regenerate_round=regenerate_round)
            # 模型调用在线程池执行，复制上下文让线程日志和取消信号都归属当前账号与轮次。
            with chat_cancel_context(cancel_event):
                if cancel_event is not None:
                    with discard_logs_after(cancel_event):
                        return await loop.run_in_executor(self._executor, copy_context().run, runner, entry, user, text)
                return await loop.run_in_executor(self._executor, copy_context().run, runner, entry, user, text)
        finally:
            if handle is not None:
                entry.turns.pop(handle.turn_id, None)
            entry.cancelling_worker = False
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

    def _portrait_context(self, user_id: int) -> str:
        if self._portraits is None:
            return '暂无'
        return format_portrait_for_prompt(self._portraits.get(user_id))

    def _run(self, entry: AccountChat, user: User, text: str):
        agents = self._agents_for(user)
        if entry.runtime is None:
            entry.runtime = self._initialize(user.id)
        runtime = entry.runtime
        # 每轮操作自己的状态副本，工作流抛错或取消时保留上一次状态。
        state = deepcopy(runtime.state)
        state['user_input'] = text
        state['user_portrait'] = self._portrait_context(user.id)
        state['regenerate_round'] = None
        result = runtime.graph.invoke(state, config={'configurable': {'agents': agents}})
        runtime.state = result
        return result

    def _run_regenerate(self, entry: AccountChat, user: User, _text: str, *, regenerate_round: int):
        agents = self._agents_for(user)
        if entry.runtime is None:
            entry.runtime = self._initialize(user.id)
        runtime = entry.runtime
        state = deepcopy(runtime.state)
        state['user_input'] = ''
        state['user_portrait'] = self._portrait_context(user.id)
        state['regenerate_round'] = regenerate_round
        result = runtime.graph.invoke(state, config={'configurable': {'agents': agents}})
        runtime.state = result
        return result
