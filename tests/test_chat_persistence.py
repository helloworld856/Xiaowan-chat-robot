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
        self.db.cursor.return_value.fetchall.return_value = []
        self.db.cursor.return_value.fetchone.return_value = None
        self.dependencies = {
            'log_config': SimpleNamespace(logger=logging.getLogger('persistence-test')),
            'agent_config': ModuleType('agent_config'),
            'persona_config': SimpleNamespace(persona=SimpleNamespace(BOT_NAME='小晚')),
            'database_config': SimpleNamespace(init_db=lambda: self.db),
        }
        module = load_module(ROOT / 'backend/memory_config/memory.py', self.dependencies)
        self.memory = module.memorier
        self.message = {'conversation_round': 1, 'user': '你好', 'assistant': ['你好呀']}

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
