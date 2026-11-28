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
from app.chat.attachments import MAX_ATTACHMENTS_PER_TURN, AttachmentStore
from app.chat.episodes import EpisodeMemoryStore
from app.chat.memory import MemoryManager
from app.chat.nodes import unpack_agents
from app.chat.progress import ChatCancelled, chat_cancel_context
from app.chat.state import AIChatState, create_initial_state
from app.chat.tool_context import ChatToolContext, DEFAULT_CONFIRMATIONS, chat_tool_context
from app.llm.configuration import AdminModelRuntime
from app.llm.model_catalog import default_model_catalog
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
    episode_task: asyncio.Task | None = None
    bg_cancel: Event = field(default_factory=Event)
    cancelling_worker: bool = False
    turns: dict[str, TurnHandle] = field(default_factory=dict)
    cancelled_turns: deque[str] = field(default_factory=lambda: deque(maxlen=16))


class ChatSessions:
    def __init__(self, connection, *, personal_models: PersonalModelStore | None = None,
                 admin_model: AdminModelRuntime | None = None,
                 portraits: UserPortraitStore | None = None,
                 attachments: AttachmentStore | None = None,
                 episodes: EpisodeMemoryStore | None = None,
                 search_config=None,
                 graph_factory=None, max_running=4, max_waiting=8, wait_timeout=2.0):
        if max_running < 1 or max_waiting < 0 or not wait_timeout > 0:
            raise ValueError('invalid chat capacity')
        self._connection = connection
        self._personal_models = personal_models
        self._admin_model = admin_model
        self._portraits = portraits
        self._attachments = attachments
        self._episodes = episodes
        self._search_config = search_config
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
        # 清空历史或注销账号前先终止后台印象学习与事件提炼，防止清理后旧任务把数据写回磁盘或数据库。
        entry.bg_cancel.set()
        bg_tasks = [t for t in (entry.learn_task, entry.episode_task) if t is not None and not t.done()]
        for task in bg_tasks:
            task.cancel()
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)
        try:
            yield
        finally:
            DEFAULT_CONFIRMATIONS.clear_user(user_id)
            # 丢弃缓存的历史和摘要，下一轮从数据库重新读取。
            self._users.pop(user_id, None)

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

    async def _admit(self, user: User, *, turn_id: str | None = None):
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
        return start, entry, handle

    def _finish_turn(self, entry: AccountChat, user: User, text: str, result: Any, start: float) -> None:
        logger.info('chat response_ready_ms=%.1f', (perf_counter() - start) * 1000)
        # 回复成功落库后异步触发后台印象学习与情景记忆归档，不阻塞本轮 HTTP 响应返回。
        if isinstance(result, dict) and result.get('saved') and not self._closing and not entry.maintenance:
            if self._portraits is not None and len(text.strip()) >= 5 and entry.learn_task is None:
                self._schedule_portrait_learning(entry, user, text, list(result.get('ai_response') or ()))
            if self._episodes is not None and entry.episode_task is None:
                saved_round = (result.get('message') or {}).get('conversation_round')
                if isinstance(saved_round, int):
                    max_eligible = saved_round - self._episodes.config.recent_window_rounds
                    if max_eligible >= 0:
                        self._schedule_episode_extraction(entry, user, max_eligible)

    def _supports_vision_for(self, user: User) -> bool:
        catalog = self._personal_models.catalog if self._personal_models is not None else default_model_catalog()
        if user.role == 'admin':
            snapshot = self._admin_model.snapshot() if self._admin_model is not None else None
            if snapshot is None:
                return False
            model = snapshot[0]
            if model.model_merchant not in catalog.providers:
                return False
            default_ep = catalog.endpoint(model.model_merchant)
            effective_adapter = 'openai' if (model.base_url and model.base_url != default_ep.base_url) else default_ep.adapter
            return catalog.supports_vision(model.model_merchant, model.model_name, adapter=effective_adapter)
        if self._personal_models is None:
            return False
        personal = self._personal_models.get(user.id)
        if personal is None:
            return False
        return catalog.supports_vision(personal.model_merchant, personal.model_name, adapter=personal.adapter)

    def _prepare_turn_attachments(
        self,
        user: User,
        attachment_ids: list[str] | None,
        quoted_attachment_ids: list[str] | None,
    ) -> tuple[list[dict], list[str], list[dict]] | None:
        own_ids = list(dict.fromkeys(attachment_ids or ()))
        quoted_ids = [aid for aid in dict.fromkeys(quoted_attachment_ids or ()) if aid not in own_ids]
        if not own_ids and not quoted_ids:
            return None
        if len(own_ids) > MAX_ATTACHMENTS_PER_TURN or len(quoted_ids) > MAX_ATTACHMENTS_PER_TURN:
            raise ChatNotStarted(422, f'单条消息最多添加 {MAX_ATTACHMENTS_PER_TURN} 个附件')
        if self._attachments is None:
            raise ChatNotStarted(503, '附件服务尚未就绪，请稍后重试')

        public_items: list[dict] = []
        image_urls: list[str] = []
        documents: list[dict] = []

        for is_quoted, group in ((False, own_ids), (True, quoted_ids)):
            for att_id in group:
                meta = self._attachments.get(user.id, att_id)
                if meta is None:
                    raise ChatNotStarted(422, '部分附件不存在或已过期，请重新选择后再发送')
                if not is_quoted:
                    public_items.append(meta.to_public().model_dump(exclude_none=True))
                if meta.kind == 'image':
                    data_url = self._attachments.image_data_url(user.id, att_id)
                    if not data_url:
                        raise ChatNotStarted(422, '图片附件读取失败，请重新选择图片')
                    image_urls.append(data_url)
                elif meta.extracted_text:
                    documents.append({
                        'name': meta.name,
                        'text': meta.extracted_text,
                        'truncated': meta.truncated,
                        'quoted': is_quoted,
                    })

        if image_urls and not self._supports_vision_for(user):
            raise ChatNotStarted(
                422,
                '当前所选模型暂不支持图片识别，请切换到支持视觉的模型（如 deepseek-v4-flash），或仅发送文字与文档',
            )
        return public_items, image_urls, documents

    async def send(self, user: User, text: str, *, turn_id: str | None = None,
                   attachment_ids: list[str] | None = None,
                   quoted_attachment_ids: list[str] | None = None):
        media = self._prepare_turn_attachments(user, attachment_ids, quoted_attachment_ids)
        start, entry, handle = await self._admit(user, turn_id=turn_id)
        work = asyncio.create_task(self._execute(entry, user, text, handle=handle, media=media))
        entry.worker = work
        work.add_done_callback(self._consume_error)
        # HTTP 请求断开后，后台任务仍持有账号锁和执行位，直到本轮处理或取消收尾结束。
        result = await asyncio.shield(work)
        self._finish_turn(entry, user, text, result, start)
        return result

    async def start_stream(self, user: User, text: str, *, turn_id: str | None = None,
                           attachment_ids: list[str] | None = None,
                           quoted_attachment_ids: list[str] | None = None):
        """准入并启动流式对话，返回异步事件队列：('bubble', str) / ('done', dict) / ('cancelled', None) / ('error', Exception)。"""
        media = self._prepare_turn_attachments(user, attachment_ids, quoted_attachment_ids)
        start, entry, handle = await self._admit(user, turn_id=turn_id)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def on_bubble(bubble: str) -> None:
            if handle is not None and handle.cancel_event.is_set():
                return
            if not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, ('bubble', bubble))

        work = asyncio.create_task(self._execute(entry, user, text, handle=handle, on_bubble=on_bubble, media=media))
        entry.worker = work

        def on_done(task: asyncio.Task) -> None:
            if task.cancelled():
                queue.put_nowait(('cancelled', None))
                return
            error = task.exception()
            if error is not None:
                if isinstance(error, ChatCancelled):
                    queue.put_nowait(('cancelled', None))
                else:
                    queue.put_nowait(('error', error))
                return
            result = task.result()
            self._finish_turn(entry, user, text, result, start)
            queue.put_nowait(('done', result))

        work.add_done_callback(on_done)
        return queue

    def _schedule_episode_extraction(self, entry: AccountChat, user: User, max_eligible_round: int) -> None:
        cancel_event = entry.bg_cancel

        async def run_extract():
            try:
                if cancel_event.is_set():
                    return
                agents = self._agents_for(user)
                summary_agent, _ = unpack_agents(agents)
                loop = asyncio.get_running_loop()
                # 单次后台最多推进两批；关闭服务 drain 时跑完首批发起了的任务即可。
                for batch_idx in range(2):
                    if cancel_event.is_set() or (batch_idx > 0 and self._closing):
                        break
                    with discard_logs_after(cancel_event):
                        advanced = await loop.run_in_executor(
                            self._executor, copy_context().run,
                            partial(self._episodes.extract_and_save_batch, cancel_event=cancel_event),
                            user.id, summary_agent, max_eligible_round,
                        )
                    if not advanced:
                        break
            except asyncio.CancelledError:
                return
            except Exception as error:
                if not cancel_event.is_set():
                    logger.warning('background episode extraction skipped: %s', type(error).__name__)
            finally:
                if entry.episode_task is task:
                    entry.episode_task = None

        task = asyncio.create_task(run_extract())
        entry.episode_task = task
        task.add_done_callback(self._consume_error)

    def _schedule_portrait_learning(self, entry: AccountChat, user: User, text: str, replies: list[str]) -> None:
        cancel_event = entry.bg_cancel

        async def run_learn():
            try:
                if cancel_event.is_set():
                    return
                current = self._portraits.get(user.id)
                if not current.auto_learn:
                    return
                agents = self._agents_for(user)
                summary_agent, _ = unpack_agents(agents)
                loop = asyncio.get_running_loop()
                with discard_logs_after(cancel_event):
                    learned = await loop.run_in_executor(
                        self._executor, copy_context().run,
                        learn_portrait_from_turn, summary_agent, current, text, replies,
                    )
                if learned is not None and not cancel_event.is_set():
                    with discard_logs_after(cancel_event):
                        updated = self._portraits.update_learned(
                            user.id, learned, expected_previous=current.learned,
                        )
                        if updated is not None:
                            log_event('portrait_learned', items=len(updated.learned))
            except asyncio.CancelledError:
                return
            except Exception as error:
                if not cancel_event.is_set():
                    logger.warning('background portrait learn skipped: %s', type(error).__name__)
            finally:
                if entry.learn_task is task:
                    entry.learn_task = None

        task = asyncio.create_task(run_learn())
        entry.learn_task = task
        task.add_done_callback(self._consume_error)

    async def drain(self):
        """Finish admitted work before its database connections are closed."""
        self._closing = True
        tasks = [
            task for entry in self._users.values()
            for task in (entry.worker, entry.learn_task, entry.episode_task) if task is not None
        ]
        await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        self._executor.shutdown()

    @staticmethod
    def _consume_error(task: asyncio.Task):
        if not task.cancelled():
            task.exception()

    async def _execute(self, entry: AccountChat, user: User, text: str, *,
                       handle: TurnHandle | None = None, on_bubble=None, media=None):
        cancel_event = handle.cancel_event if handle is not None else None
        extra_kwargs: dict[str, Any] = {}
        if on_bubble is not None:
            extra_kwargs['on_bubble'] = on_bubble
        if media is not None:
            extra_kwargs['media'] = media
        run_call = partial(self._run, entry, user, text, **extra_kwargs) if extra_kwargs else partial(
            self._run, entry, user, text
        )
        try:
            loop = asyncio.get_running_loop()
            # 模型调用在线程池执行，复制上下文让线程日志和取消信号都归属当前账号与轮次。
            with chat_cancel_context(cancel_event):
                if cancel_event is not None:
                    with discard_logs_after(cancel_event):
                        return await loop.run_in_executor(self._executor, copy_context().run, run_call)
                return await loop.run_in_executor(self._executor, copy_context().run, run_call)
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

    def _run(self, entry: AccountChat, user: User, text: str, *, on_bubble=None, media=None):
        agents = self._agents_for(user)
        if entry.runtime is None:
            entry.runtime = self._initialize(user.id)
        runtime = entry.runtime
        # 每轮操作自己的状态副本，工作流抛错或取消时保留上一次状态。
        state = deepcopy(runtime.state)
        state['user_input'] = text
        state['user_portrait'] = self._portrait_context(user.id)
        if media is not None:
            public_items, image_urls, documents = media
            state['attachments'] = public_items
            state['turn_images'] = image_urls
            state['turn_documents'] = documents
        else:
            state['attachments'] = []
            state['turn_images'] = []
            state['turn_documents'] = []
        configurable = {'agents': agents}
        if on_bubble is not None:
            configurable['on_bubble'] = on_bubble
        tool_ctx = ChatToolContext(
            user_id=user.id,
            connection=self._connection,
            search_config=self._search_config,
        )
        with chat_tool_context(tool_ctx):
            result = runtime.graph.invoke(state, config={'configurable': configurable})
        runtime.state = result
        return result
