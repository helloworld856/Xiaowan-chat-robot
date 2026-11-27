import asyncio
import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from _imports import BACKEND_ROOT as ROOT

from app.chat.memory import MemoryManager, load_history


class TestCursor:
    def __init__(self, cursor, pool):
        self.cursor = cursor
        self.pool = pool

    def execute(self, statement, params=()):
        if statement.startswith('DELETE') and self.pool.fail_delete:
            raise OSError('simulated delete failure')
        if statement.startswith('INSERT INTO history_messages') and self.pool.fail_insert:
            raise OSError('simulated insert failure')
        sql = statement.replace('ON DUPLICATE KEY UPDATE', 'ON CONFLICT(user_id) DO UPDATE SET')
        return self.cursor.execute(sql.replace('%s', '?'), params).rowcount

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class SQLitePool:
    """Exercise row filtering and transactions against an isolated SQL database."""

    def __init__(self, path):
        self.path = path
        self.in_use = 0
        self.fail_delete = False
        self.fail_insert = False
        self.lose_commit_ack = False
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''
                CREATE TABLE history_messages (
                    user_id INTEGER, conversation_round INTEGER, user TEXT, assistant TEXT,
                    PRIMARY KEY (user_id, conversation_round)
                );
                CREATE TABLE abstract_messages (user_id INTEGER PRIMARY KEY, abstract TEXT);
            ''')

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path)) as db:
            self.in_use += 1

            @contextmanager
            def cursor():
                with closing(db.cursor()) as value:
                    yield TestCursor(value, self)

            def commit():
                db.commit()
                if self.lose_commit_ack:
                    self.lose_commit_ack = False
                    raise OSError('simulated loss of commit acknowledgement')

            try:
                yield SimpleNamespace(cursor=cursor, commit=commit)
            finally:
                db.rollback()
                self.in_use -= 1


class FakeAgent:
    def __init__(self, handler):
        self.handler = handler

    def invoke(self, request, config=None):
        text = self.handler(request['messages'][0]['content'])
        return {'messages': [SimpleNamespace(content=text)]}


def message(round_id, text):
    return {'conversation_round': round_id, 'user': text, 'assistant': ['first|||part', '第二条']}


class MemoryIsolationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pool = SQLitePool(Path(folder.name) / 'storage.sqlite')

    # 用相同轮次验证 SQL 隔离依赖账号 ID，而非轮次全局唯一。
    def test_two_accounts_can_save_same_round_and_restore_only_owned_history(self):
        account_a = MemoryManager(1, self.pool.connection)
        self.assertTrue(account_a.record_message(message(0, 'A')))
        self.assertTrue(account_a.record_message(message(1, 'AA')))
        self.assertFalse(account_a.record_message(message(1, 'failed duplicate')))

        account_b = MemoryManager(2, self.pool.connection)
        self.assertEqual(account_b.history_cache, [])
        self.assertTrue(account_b.record_message(message(0, 'B')))

        restored = MemoryManager(1, self.pool.connection)
        self.assertEqual(restored.history_cache, [message(0, 'A'), message(1, 'AA')])
        self.assertEqual(restored.next_round, 2)
        self.assertEqual(account_a.history_cache, restored.history_cache)
        self.assertEqual(load_history(self.pool.connection, 2, 0), [message(0, 'B')])
        self.assertEqual(load_history(self.pool.connection, 1, -1), [message(1, 'AA')])

    def test_history_sign_controls_order_and_absolute_value_limits_count(self):
        account = MemoryManager(1, self.pool.connection)
        for round_id in range(3):
            self.assertTrue(account.record_message(message(round_id, str(round_id))))
        for count, front, expected in ((2, True, [0, 1]), (2, False, [2, 1]),
                                       (-2, True, [2, 1]), (-2, False, [0, 1])):
            with self.subTest(count=count, front=front):
                rows = load_history(self.pool.connection, 1, count, front)
                self.assertEqual([row['conversation_round'] for row in rows], expected)

    def test_history_endpoint_validates_count_and_caps_unbounded_requests(self):
        from pydantic import ValidationError
        from app.api.history import HistoryRequest, history

        for count in (-10001, 10001):
            with self.subTest(count=count), self.assertRaises(ValidationError):
                HistoryRequest(num=count, front=True)
        connection = self.pool.connection
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=SimpleNamespace(connection=connection))),
                                  state=SimpleNamespace(user=SimpleNamespace(id=1)))
        for count in (-10000, 10000):
            with self.subTest(count=count), patch('app.api.history.load_history', return_value=[]) as load:
                history(HistoryRequest(num=count, front=True), request)
                load.assert_called_once_with(connection, 1, count, True)
        for count in (0, None):
            for front, expected in ((True, [0, 1]), (False, [1, 0])):
                with (self.subTest(count=count, front=front),
                      patch('app.api.history.load_history', return_value=[message(1, '新'), message(0, '旧')]) as load):
                    reply = history(HistoryRequest(num=count, front=front), request)
                    load.assert_called_once_with(connection, 1, 10000, False)
                    self.assertEqual(reply.num, count)
                    self.assertEqual([row['conversation_round'] for row in reply.history_messages], expected)

    def test_bounded_cache_compresses_oldest_database_rows_and_recovers_after_commit(self):
        with (
            patch('app.chat.memory.HISTORY_CACHE_LIMIT', 4),
            patch('app.chat.memory.HISTORY_COMPRESSION_BATCH_SIZE', 2),
        ):
            account = MemoryManager(1, self.pool.connection)
            other = MemoryManager(2, self.pool.connection)
            self.assertTrue(other.record_message(message(0, 'another account')))
            for round_id in range(7):
                self.assertTrue(account.record_message(message(round_id, f'private-{round_id}')))
            self.assertEqual([row['conversation_round'] for row in account.history_cache], [3, 4, 5, 6])
            self.assertTrue(account.has_older_history)

            restored = MemoryManager(1, self.pool.connection)
            self.assertEqual(restored.history_cache, account.history_cache)
            prompts = []

            def summarize(prompt):
                prompts.append(prompt)
                return f'summary-{len(prompts)}'

            agent = FakeAgent(summarize)
            self.pool.lose_commit_ack = True
            restored.compress_history(agent)
            restored.refresh_if_needed()
            self.assertEqual(restored.abstract_cache, 'summary-1')
            self.assertTrue(restored.has_older_history)
            with self.assertLogs('xiaowan', level='INFO') as recorded:
                restored.compress_history(agent)
            events = '\n'.join(recorded.output)
            self.assertIn('event=history_compression outcome=succeeded rounds=2 through_round=3', events)
            self.assertNotIn('private-', events)
            self.assertNotIn('summary-2', events)

            self.assertIn('private-0', prompts[0])
            self.assertIn('private-1', prompts[0])
            self.assertNotIn('private-2', prompts[0])
            self.assertNotIn('another account', prompts[0])
            self.assertIn('summary-1', prompts[1])
            self.assertIn('private-2', prompts[1])
            self.assertIn('private-3', prompts[1])
            self.assertNotIn('private-4', prompts[1])
            self.assertFalse(restored.has_older_history)
            self.assertEqual(restored.next_round, 7)
            self.assertEqual([row['conversation_round'] for row in restored.history_cache], [4, 5, 6])
            self.assertEqual(load_history(self.pool.connection, 1, 0), restored.history_cache)
            self.assertEqual(load_history(self.pool.connection, 2, 0), [message(0, 'another account')])

    def test_summary_and_history_changes_are_atomic_and_scoped(self):
        account_a = MemoryManager(1, self.pool.connection)
        account_b = MemoryManager(2, self.pool.connection)
        for round_id in range(2):
            self.assertTrue(account_a.record_message(message(round_id, 'private-A')))
            self.assertTrue(account_b.record_message(message(round_id, 'private-B')))
        account_b.compress_history(FakeAgent(lambda _: 'existing-summary-B'))

        def summarize(prompt):
            self.assertEqual(self.pool.in_use, 0)
            self.assertIn('private-A', prompt)
            self.assertNotIn('private-B', prompt)
            return 'summary-A'

        self.pool.fail_delete = True
        account_a.compress_history(FakeAgent(summarize))
        self.assertEqual(account_a.abstract_cache, '')
        self.assertEqual(len(account_a.history_cache), 2)
        restored = MemoryManager(1, self.pool.connection)
        self.assertEqual(restored.abstract_cache, '')
        self.assertEqual(len(restored.history_cache), 2)

        self.pool.fail_delete = False
        account_a.compress_history(FakeAgent(summarize))
        restored = MemoryManager(1, self.pool.connection)
        self.assertEqual(restored.abstract_cache, 'summary-A')
        self.assertEqual(restored.history_cache, [message(1, 'private-A')])
        self.assertEqual(restored.next_round, 2)
        other = MemoryManager(2, self.pool.connection)
        self.assertEqual(other.abstract_cache, 'existing-summary-B')
        self.assertEqual(other.history_cache, [message(1, 'private-B')])

    def test_summary_accepts_model_text_blocks(self):
        account = MemoryManager(1, self.pool.connection)
        for round_id in range(2):
            self.assertTrue(account.record_message(message(round_id, 'previous message')))
        account.compress_history(FakeAgent(lambda _: [
            {'type': 'reasoning', 'reasoning': 'private reasoning'},
            {'type': 'text', 'text': '有效摘要'},
        ]))

        restored = MemoryManager(1, self.pool.connection)
        self.assertEqual(restored.abstract_cache, '有效摘要')
        self.assertEqual(restored.history_cache, [message(1, 'previous message')])


@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'requires LangGraph')
class GraphOutputTests(unittest.TestCase):
    def test_token_usage_accepts_standard_metadata_and_ignores_invalid_values(self):
        from app.chat.nodes import get_total_tokens

        self.assertEqual(get_total_tokens(SimpleNamespace(
            usage_metadata={'total_tokens': 42},
            response_metadata={'token_usage': {'total_tokens': None}},
        )), 42)
        self.assertEqual(get_total_tokens(SimpleNamespace(
            response_metadata={'token_usage': {'total_tokens': 23}},
        )), 23)
        for invalid in (None, '23', {}, True, -1):
            with self.subTest(invalid=invalid):
                self.assertEqual(get_total_tokens(SimpleNamespace(
                    response_metadata={'token_usage': {'total_tokens': invalid}},
                )), 0)


@unittest.skipUnless(
    all(importlib.util.find_spec(name) is not None for name in ('langgraph', 'tokenizers')),
    'install declared LangGraph and Transformers dependencies for the real graph test',
)
class ConversationGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_model_field_types_do_not_break_following_turn(self):
        from app.accounts.repository import User
        from app.chat.scheduler import ChatSessions

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pool = SQLitePool(Path(folder.name) / 'graph.sqlite')
        user = User(1, 'First', 'admin')
        sessions = ChatSessions(pool.connection)
        self.addAsyncCleanup(sessions.drain)
        monologue = '[' * 600 + '"nested"' + ']' * 600
        reply = '{"reply":["回复"],"inner monologue":' + monologue + '}'
        agents = (
            FakeAgent(lambda _: '{"intent":{}}'),
            FakeAgent(lambda _: 'summary'),
            FakeAgent(lambda _: reply),
        )
        with (
            patch.object(ChatSessions, '_snapshot_agents', return_value=agents),
            patch('app.chat.nodes.count_tokens', return_value=1),
        ):
            first = await sessions.send(user, 'first turn')
            self.assertTrue((await sessions.send(user, 'second turn'))['saved'])
        self.assertTrue(first['saved'])
        self.assertEqual(first['ai_response'], ['回复'])
        self.assertIsInstance(first['analysis_result']['intent'], str)
        self.assertEqual(first['ai_monologue'], [])

    async def test_next_turn_follows_committed_history_after_failed_save(self):
        from app.accounts.repository import User
        from app.chat.scheduler import ChatSessions

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pool = SQLitePool(Path(folder.name) / 'graph.sqlite')
        user = User(1, 'First', 'admin')
        sessions = ChatSessions(pool.connection)
        self.addAsyncCleanup(sessions.drain)
        prompts = []

        def analyze(prompt):
            prompts.append(prompt)
            return '{"intent":"对话"}'

        agents = (FakeAgent(analyze), FakeAgent(lambda _: 'summary'), FakeAgent(lambda _: '{"reply":["回复"]}'))
        with (
            patch.object(ChatSessions, '_snapshot_agents', return_value=agents),
            patch('app.chat.nodes.count_tokens', return_value=1),
        ):
            pool.fail_insert = True
            failed = await sessions.send(user, 'failed insert')
            self.assertFalse(failed['saved'])
            self.assertEqual(failed['message']['conversation_round'], 0)
            pool.fail_insert = False
            pool.lose_commit_ack = True
            self.assertFalse((await sessions.send(user, 'committed without acknowledgement'))['saved'])
            self.assertNotIn('failed insert', prompts[-1])
            saved = await sessions.send(user, 'next turn')
            self.assertTrue(saved['saved'])
            self.assertEqual(saved['message']['conversation_round'], 1)
            self.assertIn('committed without acknowledgement', prompts[-1])
        self.assertEqual(
            [(row['conversation_round'], row['user']) for row in load_history(pool.connection, user.id, 0)],
            [(0, 'committed without acknowledgement'), (1, 'next turn')],
        )

    async def test_next_turn_recovers_summary_after_lost_commit_acknowledgement(self):
        from app.accounts.repository import User
        from app.chat.scheduler import ChatSessions
        from app.chat.workflow import create_graph

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pool = SQLitePool(Path(folder.name) / 'graph.sqlite')
        user = User(1, 'First', 'admin')
        sessions = ChatSessions(pool.connection, graph_factory=partial(create_graph, compression_tokens=8))
        self.addAsyncCleanup(sessions.drain)
        prompts = []

        def analyze(prompt):
            prompts.append(prompt)
            return '{"intent":"对话"}'

        agents = (
            FakeAgent(analyze),
            FakeAgent(lambda _: 'committed summary'),
            FakeAgent(lambda _: '{"reply":["回复"]}'),
        )
        with (
            patch.object(ChatSessions, '_snapshot_agents', return_value=agents),
            patch('app.chat.nodes.count_tokens', side_effect=(1, 1, 0)),
        ):
            await sessions.send(user, 'first turn')
            await sessions.send(user, 'second turn')
            pool.lose_commit_ack = True
            self.assertTrue((await sessions.send(user, 'compression turn'))['saved'])
            await sessions.send(user, 'next turn')
        self.assertIn('committed summary', prompts[-1])
        self.assertNotIn('first turn', prompts[-1])
        self.assertIn('second turn', prompts[-1])

    async def test_same_account_reuses_graph_and_other_account_remains_isolated(self):
        from app.chat.scheduler import ChatSessions

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pool = SQLitePool(Path(folder.name) / 'graph.sqlite')
        from app.accounts.repository import User

        first_user = User(1, 'First', 'admin')
        second_user = User(2, 'Second', 'admin')
        sessions = ChatSessions(pool.connection, wait_timeout=3)
        first_entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        observed = []

        def analysis(prompt):
            observed.append(prompt)
            if 'private-A-first' in prompt and not first_entered.is_set():
                first_entered.set()
                release.wait(3)
            return '{"intent":"对话"}'

        agents = (
            FakeAgent(analysis),
            FakeAgent(lambda _: 'summary'),
            FakeAgent(lambda _: '{"reply":["回复"]}'),
        )
        with (
            patch.object(ChatSessions, '_snapshot_agents', return_value=agents),
            patch('app.chat.nodes.count_tokens', return_value=1),
        ):
            first = asyncio.create_task(sessions.send(first_user, 'private-A-first'))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(first_entered.wait, 3))
                account_a = sessions._users[1].runtime
                second = asyncio.create_task(sessions.send(first_user, 'private-A-second'))
                other_result = await asyncio.wait_for(sessions.send(second_user, 'private-B'), 1)
                self.assertFalse(first.done())
                self.assertFalse(second.done())
            finally:
                release.set()
                first_result = await first
                if second is not None:
                    second_result = await second
            account_b = sessions._users[2].runtime
            self.assertIs(sessions._users[1].runtime, account_a)
            self.assertIsNot(account_a.graph, account_b.graph)
            self.assertIsNot(account_a.memorier, account_b.memorier)
            self.assertIsNot(account_a.state['message'], account_b.state['message'])
            self.assertTrue(all(result['saved'] for result in (first_result, second_result, other_result)))
            self.assertEqual([row['conversation_round'] for row in account_a.memorier.history_cache], [0, 1])
            self.assertEqual([row['conversation_round'] for row in account_b.memorier.history_cache], [0])
            b_prompt = next(prompt for prompt in observed if 'private-B' in prompt)
            a_prompt = next(prompt for prompt in observed if 'private-A-second' in prompt)
            self.assertNotIn('private-A', b_prompt)
            self.assertIn('private-A-first', a_prompt)
