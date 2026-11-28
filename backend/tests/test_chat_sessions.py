import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from _imports import BACKEND_ROOT as ROOT


from app.accounts.repository import User

FIRST_USER = User(1, 'First', 'admin')
SECOND_USER = User(2, 'Second', 'admin')


class ChatSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app.chat.scheduler import ChatSessions
        self.sessions = ChatSessions(None, max_running=2, max_waiting=2, wait_timeout=0.2)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    async def wait_until_entered(self, entered):
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))

    async def test_different_accounts_can_run_while_first_is_blocked(self):
        entered = threading.Event()

        def run(self_, entry, user, text):
            if user.id == FIRST_USER.id:
                entered.set()
                self.release.wait(3)
            return text

        with patch.object(type(self.sessions), '_run', run):
            first = asyncio.create_task(self.sessions.send(FIRST_USER, 'first'))
            try:
                await self.wait_until_entered(entered)
                self.assertEqual(await asyncio.wait_for(self.sessions.send(SECOND_USER, 'other'), 1), 'other')
                self.assertFalse(first.done())
            finally:
                self.release.set()
                await first

    # 测试调用方取消后，实际处理线程仍保有账号执行权。
    async def test_cancellation_keeps_account_busy_until_worker_finishes(self):
        from app.chat.scheduler import ChatNotStarted
        entered = threading.Event()
        calls = []

        def run(self_, entry, user, text):
            calls.append(text)
            if text == 'first':
                entered.set()
                self.release.wait(3)
            return text

        with patch.object(type(self.sessions), '_run', run):
            first = asyncio.create_task(self.sessions.send(FIRST_USER, 'first'))
            try:
                await self.wait_until_entered(entered)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                with self.assertRaises(ChatNotStarted) as busy:
                    async with self.sessions.maintenance(FIRST_USER.id):
                        self.fail('cannot clear while a detached chat is running')
                self.assertEqual(busy.exception.status_code, 409)
                with self.assertRaises(ChatNotStarted) as timed_out:
                    await self.sessions.send(FIRST_USER, 'must not run')
                self.assertEqual(timed_out.exception.status_code, 503)
                self.assertEqual(calls, ['first'])
            finally:
                self.release.set()
            self.assertEqual(await self.sessions.send(FIRST_USER, 'next'), 'next')
            self.assertEqual(calls, ['first', 'next'])

    async def test_maintenance_discards_cached_runtime_and_blocks_new_turns_for_the_account(self):
        from app.chat.scheduler import ChatNotStarted

        runtimes = []

        def run(self_, entry, user, text):
            runtimes.append(entry.runtime)
            entry.runtime = user.username
            return text

        with patch.object(type(self.sessions), '_run', run):
            await self.sessions.send(FIRST_USER, 'before')
            async with self.sessions.maintenance(FIRST_USER.id):
                with self.assertRaises(ChatNotStarted):
                    await self.sessions.send(FIRST_USER, 'blocked')
                self.assertEqual(await self.sessions.send(SECOND_USER, 'other'), 'other')
            await self.sessions.send(FIRST_USER, 'after')
        self.assertEqual(runtimes, [None, None, None])

    async def test_global_and_account_queue_limits(self):
        from app.chat.scheduler import ChatNotStarted, ChatSessions
        self.sessions = ChatSessions(None, max_running=1, max_waiting=1, wait_timeout=1)
        entered = threading.Event()

        def run(self_, entry, user, text):
            if text == 'first':
                entered.set()
                self.release.wait(3)
            return text

        with patch.object(type(self.sessions), '_run', run):
            first = asyncio.create_task(self.sessions.send(FIRST_USER, 'first'))
            waiting = None
            try:
                await self.wait_until_entered(entered)
                waiting = asyncio.create_task(self.sessions.send(FIRST_USER, 'second'))
                await asyncio.sleep(0)
                with self.assertRaises(ChatNotStarted) as duplicate:
                    await self.sessions.send(FIRST_USER, 'third')
                self.assertEqual(duplicate.exception.status_code, 429)
                with self.assertRaises(ChatNotStarted) as full:
                    await self.sessions.send(SECOND_USER, 'busy')
                self.assertEqual(full.exception.status_code, 503)
            finally:
                self.release.set()
                await first
                if waiting is not None:
                    self.assertEqual(await waiting, 'second')

    async def test_waiting_turn_is_rejected_if_it_gets_a_slot_after_deadline(self):
        from app.chat.scheduler import ChatNotStarted, ChatSessions
        self.sessions = ChatSessions(None, max_running=1, wait_timeout=1)
        self.addAsyncCleanup(self.sessions.drain)
        entered = threading.Event()
        calls = []
        now = 10.0

        def run(self_, entry, user, text):
            calls.append(text)
            if text == 'first':
                entered.set()
                self.release.wait(3)
            return text

        with (
            patch.object(ChatSessions, '_run', run),
            patch('app.chat.scheduler.perf_counter', side_effect=lambda: now),
        ):
            first = asyncio.create_task(self.sessions.send(FIRST_USER, 'first'))
            try:
                await self.wait_until_entered(entered)
                queued = asyncio.create_task(self.sessions.send(SECOND_USER, 'too late'))
                await asyncio.sleep(0)
                now = 11.1
            finally:
                self.release.set()
                await first
            with self.assertRaises(ChatNotStarted) as late:
                await queued
            self.assertEqual(late.exception.status_code, 503)
            self.assertEqual(await self.sessions.send(SECOND_USER, 'next'), 'next')
            self.assertEqual(calls, ['first', 'next'])

    async def test_shutdown_finishes_an_admitted_turn_and_rejects_new_turns(self):
        from app.chat.scheduler import ChatNotStarted
        entered = threading.Event()

        def run(self_, entry, user, text):
            entered.set()
            self.release.wait(3)
            return text

        with patch.object(type(self.sessions), '_run', run):
            first = asyncio.create_task(self.sessions.send(FIRST_USER, 'first'))
            shutdown = None
            try:
                await self.wait_until_entered(entered)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                shutdown = asyncio.create_task(self.sessions.drain())
                await asyncio.sleep(0)
                self.assertFalse(shutdown.done())
                with self.assertRaises(ChatNotStarted):
                    await self.sessions.send(SECOND_USER, 'must not run')
            finally:
                self.release.set()
                if shutdown is not None:
                    await shutdown

    async def test_failed_initialization_can_be_retried(self):
        from app.chat.state import create_initial_state
        graph = SimpleNamespace(invoke=lambda state, config: state)
        runtime = SimpleNamespace(graph=graph, state=create_initial_state())
        with (
            patch.object(type(self.sessions), '_snapshot_agents', return_value=(1, 2, 3)),
            patch.object(type(self.sessions), '_initialize', side_effect=[OSError('DB down'), runtime]) as build,
        ):
            with self.assertRaises(OSError):
                await self.sessions.send(FIRST_USER, 'first attempt')
            result = await self.sessions.send(FIRST_USER, 'retry')
            self.assertEqual(result['user_input'], 'retry')
            self.assertEqual(build.call_count, 2)


class ChatStateTests(unittest.TestCase):
    def test_initial_state_has_independent_nested_values(self):
        from app.chat.state import create_initial_state
        first = create_initial_state(1)
        second = create_initial_state(7)
        first['message']['assistant'].append('private')
        first['ai_response'].append('private')
        self.assertEqual(second['message']['assistant'], [])
        self.assertEqual(second['message']['conversation_round'], 7)
        self.assertEqual(second['ai_response'], [])


class ChatRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_rejects_blank_and_oversized_input_before_scheduling(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.api.chat import router
        from app.system_config import SystemConfig

        calls = []

        async def send(user, text):
            self.assertEqual(user.id, 42)
            calls.append(text)
            return {'ai_response': ['回复'], 'message': {'conversation_round': 1}, 'saved': text != '晚'}

        app = FastAPI()
        app.state.system_config = SystemConfig(chat={'max_message_chars': 4})
        app.state.chat = SimpleNamespace(send=send)
        app.include_router(router)

        @app.middleware('http')
        async def authenticate(request, call_next):
            request.state.user = SimpleNamespace(id=42)
            return await call_next(request)

        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            for text in ('', ' \n\t', '晚' * 5):
                with self.subTest(length=len(text)):
                    response = await client.post('/chat', json={'user_input': text})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()['detail']['code'], 'CHAT_NOT_STARTED')
            response = await client.post('/chat', json={'user_input': ' 😀晚 '})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['conversation_round'], 1)
            response = await client.post('/chat', json={'user_input': '晚'})
            self.assertEqual(response.json(), {'response': ['回复'], 'conversation_round': None, 'saved': False})
        self.assertEqual(calls, [' 😀晚 ', '晚'])


    async def test_cancel_discards_in_flight_turn_without_saving(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.api.chat import router as chat_router
        from app.chat.progress import check_cancelled
        from app.chat.scheduler import ChatSessions
        from app.system_config import SystemConfig

        sessions = ChatSessions(None, max_running=2, max_waiting=2, wait_timeout=1.0)
        self.addAsyncCleanup(sessions.drain)
        tool_entered = threading.Event()
        release_tool = threading.Event()
        self.addCleanup(release_tool.set)
        saved_rounds = []

        def run(self_, entry, user, text):
            check_cancelled()
            if text == 'cancel-me':
                tool_entered.set()
                release_tool.wait(3)
            check_cancelled()
            saved_rounds.append(text)
            return {
                'ai_response': ['晴天回复'],
                'message': {'conversation_round': 0, 'user': text, 'assistant': ['晴天回复'], 'created_at': 1764150000500},
                'saved': True,
            }

        app = FastAPI()
        app.state.system_config = SystemConfig()
        app.state.chat = sessions
        app.include_router(chat_router)

        @app.middleware('http')
        async def authenticate(request, call_next):
            request.state.user = FIRST_USER
            return await call_next(request)

        with patch.object(ChatSessions, '_run', run):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                completed = await client.post('/chat', json={'user_input': '查杭州天气', 'turn_id': 'turn_ok'})
                self.assertEqual(completed.status_code, 200)
                self.assertEqual(completed.json(), {
                    'response': ['晴天回复'],
                    'conversation_round': 0,
                    'saved': True,
                    'created_at': 1764150000500,
                })

                # 进行中的请求取消后返回 499 CHAT_CANCELLED，且不写入记录
                in_flight = asyncio.create_task(
                    client.post('/chat', json={'user_input': 'cancel-me', 'turn_id': 'turn_cancel'})
                )
                self.assertTrue(await asyncio.to_thread(tool_entered.wait, 3))
                cancel_res = await client.post('/chat/cancel', json={'turn_id': 'turn_cancel'})
                self.assertEqual(cancel_res.json(), {'ok': True, 'cancelled': True})
                release_tool.set()
                cancelled_res = await in_flight
                self.assertEqual(cancelled_res.status_code, 499)
                self.assertEqual(cancelled_res.json()['detail']['code'], 'CHAT_CANCELLED')
                self.assertEqual(saved_rounds, ['查杭州天气'])

    async def test_chat_stream_emits_bubbles_then_done_and_validates_before_streaming(self):
        import json as _json
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.api.chat import router as chat_router
        from app.chat.scheduler import ChatSessions
        from app.system_config import SystemConfig

        sessions = ChatSessions(None, max_running=2, max_waiting=2, wait_timeout=1.0)
        self.addAsyncCleanup(sessions.drain)

        def run(self_, entry, user, text, *, on_bubble=None):
            import time as _time
            _time.sleep(0.08)
            if on_bubble is not None:
                on_bubble('第一条气泡')
                on_bubble('第二条气泡')
            return {
                'ai_response': ['第一条气泡', '第二条气泡'],
                'message': {
                    'conversation_round': 3,
                    'user': text,
                    'assistant': ['第一条气泡', '第二条气泡'],
                    'created_at': 1764150000900,
                },
                'saved': True,
            }

        app = FastAPI()
        app.state.system_config = SystemConfig(chat={'max_message_chars': 20})
        app.state.chat = sessions
        app.include_router(chat_router)

        @app.middleware('http')
        async def authenticate(request, call_next):
            request.state.user = FIRST_USER
            return await call_next(request)

        with (
            patch.object(ChatSessions, '_run', run),
            patch('app.api.chat.STREAM_HEARTBEAT_SECONDS', 0.03),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                bad = await client.post('/chat/stream', json={'user_input': '   '})
                self.assertEqual(bad.status_code, 422)
                self.assertEqual(bad.json()['detail']['code'], 'CHAT_NOT_STARTED')

                res = await client.post('/chat/stream', json={'user_input': '你好呀', 'turn_id': 'turn_stream_1'})
                self.assertEqual(res.status_code, 200)
                self.assertIn('text/event-stream', res.headers['content-type'])
                self.assertIn(': ping', res.text)
                events = []
                for block in res.text.strip().split('\n\n'):
                    if block.strip().startswith(':'):
                        continue
                    ev, data = None, None
                    for line in block.splitlines():
                        if line.startswith('event:'):
                            ev = line.split(':', 1)[1].strip()
                        elif line.startswith('data:'):
                            data = _json.loads(line.split(':', 1)[1].strip())
                    events.append((ev, data))
                self.assertEqual(events, [
                    ('bubble', {'text': '第一条气泡'}),
                    ('bubble', {'text': '第二条气泡'}),
                    ('done', {
                        'response': ['第一条气泡', '第二条气泡'],
                        'conversation_round': 3,
                        'saved': True,
                        'created_at': 1764150000900,
                    }),
                ])
