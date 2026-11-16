import asyncio
import importlib.util
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'app'))

from memory_config import MemoryManager, load_history


class TestCursor:
    def __init__(self, cursor, pool):
        self.cursor = cursor
        self.pool = pool

    def execute(self, statement, params=()):
        if statement.startswith('DELETE') and self.pool.fail_delete:
            raise OSError('simulated delete failure')
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

            try:
                yield SimpleNamespace(cursor=cursor, commit=db.commit)
            finally:
                db.rollback()
                self.in_use -= 1


class FakeAgent:
    def __init__(self, handler):
        self.handler = handler

    def invoke(self, request):
        text = self.handler(request['messages'][0]['content'])
        return {'messages': [SimpleNamespace(content=text)]}


def message(round_id, text):
    return {'conversation_round': round_id, 'user': text, 'assistant': ['first|||part', '第二条']}


class MemoryIsolationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pool = SQLitePool(Path(folder.name) / 'storage.sqlite')

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


@unittest.skipUnless(
    all(importlib.util.find_spec(name) is not None for name in ('langgraph', 'transformers')),
    'install declared LangGraph and Transformers dependencies for the real graph test',
)
class ConversationGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_account_reuses_graph_and_other_account_remains_isolated(self):
        from core.session import ChatSessions

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pool = SQLitePool(Path(folder.name) / 'graph.sqlite')
        from auth_config import User

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
            patch('graph_config.nodes.count_tokens', return_value=1),
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
