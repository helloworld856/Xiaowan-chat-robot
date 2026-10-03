"""只隔离数据库和模型依赖，避免测试触发启动时清空数据。"""
import importlib.util
import logging
from pathlib import Path
import sys
from time import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
logging.getLogger('persistence-test').addHandler(logging.NullHandler())


def load_module(path, dependencies):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module


class ChatPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.cursor = self.db.cursor.return_value
        self.cursor.__enter__.return_value = self.cursor
        self.cursor.close.return_value = None
        self.cursor.__exit__.side_effect = lambda *args: self.cursor.close()
        self.db.cursor.return_value.fetchall.return_value = []
        self.db.cursor.return_value.fetchone.return_value = None
        logger_dependency = SimpleNamespace(logger=logging.getLogger('persistence-test'))
        rollback_db = load_module(ROOT / 'backend/database_config/init_mysql.py', {
            'pymysql': SimpleNamespace(), 'config': SimpleNamespace(configer=None),
            'log_config': logger_dependency,
        }).rollback_db
        self.dependencies = {
            'log_config': logger_dependency,
            'agent_config': ModuleType('agent_config'),
            'persona_config': SimpleNamespace(persona=SimpleNamespace(BOT_NAME='小晚')),
            'database_config': SimpleNamespace(init_db=lambda: self.db, rollback_db=rollback_db),
        }
        module = load_module(ROOT / 'backend/memory_config/memory.py', self.dependencies)
        self.memory = module.memorier
        self.message = {'conversation_round': 1, 'user': '你好', 'assistant': ['你好呀']}
        self.text_agent = self.dependencies['agent_config'].text_agent = MagicMock()
        self.text_agent.invoke.return_value = {'messages': [SimpleNamespace(content='新摘要')]}

    def test_successful_commit_reports_saved(self):
        self.assertIs(self.memory.record_message(self.message), True)

    def test_failed_commit_reports_unsaved(self):
        self.db.commit.side_effect = RuntimeError('数据库连接中断')
        self.assertIs(self.memory.record_message(self.message), False)

    def test_failed_rollback_still_reports_unsaved(self):
        self.db.commit.side_effect = RuntimeError('数据库连接中断')
        self.db.rollback.side_effect = RuntimeError('连接已关闭')
        self.assertIs(self.memory.record_message(self.message), False)

    def test_later_round_and_reply_changes_do_not_change_cached_history(self):
        self.memory.record_message(self.message)
        self.message['conversation_round'] += 1
        self.message['assistant'].append('下一条')
        self.assertEqual(self.memory.history_cache, [
            {'conversation_round': 1, 'user': '你好', 'assistant': ['你好呀']}
        ])

    def test_database_cursors_close_on_success_and_failure(self):
        operations = (
            lambda: self.memory.record_message(self.message),
            lambda: self.memory.load_history(0),
            self.memory.load_abstract,
        )
        for operation in operations:
            for fails in (False, True):
                with self.subTest(operation=operation, fails=fails):
                    self.cursor.close.reset_mock()
                    self.cursor.execute.side_effect = RuntimeError('查询失败') if fails else None
                    operation()
                    self.cursor.close.assert_called_once()

    def test_history_query_keeps_existing_order_and_limit_rules(self):
        self.cursor.fetchall.return_value = [(1, '你好', '你好呀|||今天怎么样')]
        for num, front, direction, params in (
            (0, True, 'asc', None), (2, True, 'asc', [2]),
            (2, False, 'desc', [2]), (-1, True, 'desc', [1]),
            (-1, False, 'asc', [1]),
        ):
            with self.subTest(num=num, front=front):
                result = self.memory.load_history(num, front)
                expected_sql = f'select * from history_messages order by conversation_round {direction}'
                if params is not None:
                    expected_sql += ' limit %s'
                self.cursor.execute.assert_called_with(expected_sql, params)
                self.assertEqual(result, [
                    {'conversation_round': 1, 'user': '你好', 'assistant': ['你好呀', '今天怎么样']},
                ])

    def test_compression_skips_fewer_than_two_records(self):
        for length in (0, 1):
            with self.subTest(length=length):
                self.cursor.fetchone.return_value = (length,)
                self.memory.compress_history()
                self.text_agent.invoke.assert_not_called()
                self.db.commit.assert_not_called()
                self.assertTrue(self.memory.cache_valid)

    def test_compression_keeps_half_the_records_and_invalidates_cache(self):
        self.cursor.fetchone.side_effect = [(4,), ('旧摘要',)]
        self.cursor.fetchall.return_value = [(1, '一', '回复一'), (2, '二', '回复二')]
        self.memory.compress_history()
        self.cursor.execute.assert_called_with(
            'delete from history_messages order by conversation_round limit %s;', [2],
        )
        self.db.commit.assert_called_once()
        self.assertFalse(self.memory.cache_valid)

    def test_failed_history_read_does_not_delete_records_during_compression(self):
        self.cursor.fetchone.side_effect = [(4,), ('旧摘要',)]

        def execute(sql, params=None):
            if sql.startswith('select * from history_messages'):
                raise RuntimeError('读取历史失败')

        self.cursor.execute.side_effect = execute
        self.memory.compress_history()
        self.db.commit.assert_not_called()
        self.text_agent.invoke.assert_not_called()
        self.assertTrue(self.memory.cache_valid)

    def test_compression_handles_rollback_failure(self):
        self.cursor.execute.side_effect = RuntimeError('查询失败')
        self.db.rollback.side_effect = RuntimeError('连接已关闭')
        try:
            self.memory.compress_history()
        except RuntimeError as error:
            self.fail(f'压缩失败不应因回滚再次抛错: {error}')
        self.assertTrue(self.memory.cache_valid)

    def test_failed_summary_read_does_not_delete_history(self):
        self.cursor.fetchone.return_value = (4,)
        self.cursor.fetchall.return_value = [(1, '一', '回复一'), (2, '二', '回复二')]

        def execute(sql, params=None):
            if sql.startswith('select * from abstract_messages'):
                raise RuntimeError('读取摘要失败')

        self.cursor.execute.side_effect = execute
        self.memory.compress_history()
        self.db.commit.assert_not_called()
        self.text_agent.invoke.assert_not_called()
        self.assertTrue(self.memory.cache_valid)

    def test_missing_summary_record_does_not_delete_history(self):
        self.cursor.fetchone.side_effect = [(4,), None]
        self.cursor.fetchall.return_value = [(1, '一', '回复一'), (2, '二', '回复二')]
        self.memory.compress_history()
        self.db.commit.assert_not_called()
        self.text_agent.invoke.assert_not_called()
        self.assertTrue(self.memory.cache_valid)

    def test_workflow_preserves_reply_and_exposes_save_result(self):
        dependencies = {
            **self.dependencies,
            'memory_config': SimpleNamespace(memorier=self.memory),
            'state_config': SimpleNamespace(AIChatState=dict),
            'config': SimpleNamespace(configer=SimpleNamespace()),
            'utils': SimpleNamespace(count_tokens=lambda text: 0, safe_parse_json=lambda text, default: default),
        }
        nodes = load_module(ROOT / 'backend/graph_config/nodes.py', dependencies)
        for fails in (False, True):
            with self.subTest(database_fails=fails):
                self.db.commit.side_effect = RuntimeError('数据库连接中断') if fails else None
                state = {
                    'message': {**self.message, 'assistant': self.message['assistant'].copy()},
                    'ai_response': ['你好呀'], 'ai_monologue': [],
                    'ai_emotion': '平静', 'ai_action': '', 'total_tokens': 0,
                    'start_time': time(),
                }
                result = nodes.end_node(state)
                self.assertEqual(result['ai_response'], ['你好呀'])
                self.assertIs(result.get('saved'), not fails)
                self.assertEqual(result['message']['conversation_round'], 2)


if __name__ == '__main__':
    unittest.main()
