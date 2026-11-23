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
                with self.assertRaises(ChatNotStarted) as timed_out:
                    await self.sessions.send(FIRST_USER, 'must not run')
                self.assertEqual(timed_out.exception.status_code, 503)
                self.assertEqual(calls, ['first'])
            finally:
                self.release.set()
            self.assertEqual(await self.sessions.send(FIRST_USER, 'next'), 'next')
            self.assertEqual(calls, ['first', 'next'])

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
        first['analysis_result']['intent'] = 'private'
        self.assertEqual(second['message']['assistant'], [])
        self.assertEqual(second['message']['conversation_round'], 7)
        self.assertEqual(second['analysis_result'], {})


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
