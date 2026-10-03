"""隔离外部服务，检查后端的解析、路径和模型返回值。"""
import asyncio
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from test_chat_persistence import load_module

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger('persistence-test')


class ConfigPathTests(unittest.TestCase):
    def test_resource_paths_do_not_depend_on_working_directory(self):
        for directory in (ROOT, ROOT / 'backend', ROOT / 'frontend'):
            with self.subTest(directory=directory):
                previous_directory = os.getcwd()
                try:
                    os.chdir(directory)
                    config = load_module(ROOT / 'backend/config/cfg.py', {}).configer
                    self.assertEqual(Path(config.root_dir_path), ROOT / 'backend')
                    self.assertTrue(Path(config.tokenizer_path).is_dir())
                    self.assertEqual(Path(config.log_save_path).parent, ROOT / 'backend/log_config')
                finally:
                    os.chdir(previous_directory)


class JsonParserTests(unittest.TestCase):
    def setUp(self):
        self.parse = load_module(
            ROOT / 'backend/utils/json_parser.py',
            {'log_config': SimpleNamespace(logger=LOGGER)},
        ).safe_parse_json

    def test_non_object_json_uses_default(self):
        default = {'reply': ['备用回复']}
        for text in ('[]', '123', 'true', '"文字"', 'null'):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text, default), default)

    def test_fallback_does_not_share_nested_lists(self):
        default = {'reply': ['备用回复'], 'inner monologue': []}
        for text in ('', '无法解析'):
            with self.subTest(text=text):
                result = self.parse(text, default)
                result['reply'].append('后来修改的内容')
                self.assertEqual(default['reply'], ['备用回复'])

    def test_tolerant_parsing_preserves_words_inside_strings(self):
        result = self.parse("{'reply': ['true love, false alarm, null'], 'flag': true}")
        self.assertEqual(result, {'reply': ['true love, false alarm, null'], 'flag': True})

    def test_existing_json_and_code_block_formats_still_work(self):
        for text in ('{"reply": ["你好"]}', '```json\n{"reply": ["你好"]}\n```', "{'reply': ['你好']}"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), {'reply': ['你好']})

    def test_wrapped_json_preserves_chinese_punctuation(self):
        result = self.parse('回复如下：{"reply": ["你好，今天几点：三点？"]}')
        self.assertEqual(result, {'reply': ['你好，今天几点：三点？']})


class GeneratedReplyTests(unittest.TestCase):
    def setUp(self):
        config = load_module(ROOT / 'backend/config/cfg.py', {}).configer
        parser = load_module(
            ROOT / 'backend/utils/json_parser.py', {'log_config': SimpleNamespace(logger=LOGGER)},
        ).safe_parse_json
        self.agent = MagicMock()
        self.nodes = load_module(ROOT / 'backend/graph_config/nodes.py', {
            'config': SimpleNamespace(configer=config),
            'log_config': SimpleNamespace(logger=LOGGER),
            'state_config': SimpleNamespace(AIChatState=dict),
            'persona_config': SimpleNamespace(persona=SimpleNamespace(BOT_NAME='小晚')),
            'memory_config': SimpleNamespace(memorier=SimpleNamespace(history_cache=[], abstract_cache='')),
            'agent_config': SimpleNamespace(friend_agent=self.agent),
            'utils': SimpleNamespace(count_tokens=lambda text: 0, safe_parse_json=parser),
        })

    def generate(self, reply):
        self.agent.invoke.return_value = {'messages': [SimpleNamespace(
            content=json.dumps({'reply': reply}), response_metadata={'token_usage': {'total_tokens': 3}},
        )]}
        return self.nodes.generate({
            'user_input': '你好', 'analysis_result': {}, 'total_tokens': 0,
            'message': {'conversation_round': 1, 'user': '', 'assistant': []},
        })

    def test_invalid_reply_list_uses_fallback(self):
        for reply in ([], [''], ['   '], [None], [123], ['你好', None], ''):
            with self.subTest(reply=reply):
                result = self.generate(reply)
                self.assertEqual(result['ai_response'], ['嗯...我刚刚有点没组织好语言，你再说一遍嘛'])
                self.assertEqual(result['message']['assistant'], result['ai_response'])

    def test_valid_reply_text_and_token_count_stay_unchanged(self):
        for reply in ('你好呀', ['你好呀', '今天怎么样'], ['  你好呀  ']):
            with self.subTest(reply=reply):
                result = self.generate(reply)
                self.assertEqual(result['ai_response'], [reply] if isinstance(reply, str) else reply)
                self.assertEqual(result['total_tokens'], 3)
                self.assertEqual(result['message']['user'], '你好')


class ModelErrorTests(unittest.TestCase):
    def test_model_errors_are_classified_case_insensitively(self):
        router = SimpleNamespace(post=lambda *args, **kwargs: lambda function: function)
        create_model = MagicMock()
        model = load_module(ROOT / 'backend/app/routers/model.py', {
            'fastapi': SimpleNamespace(APIRouter=lambda: router),
            'agent_config': SimpleNamespace(switch_agents=MagicMock()),
            'agent_config.agent_factory': SimpleNamespace(create_model=create_model),
            'log_config': SimpleNamespace(logger=LOGGER),
            'schemas.model': SimpleNamespace(ModelConfigRequest=SimpleNamespace, ModelConfigResponse=SimpleNamespace),
        })
        cases = (
            ('invalid_api_key', 'API Key 无效或已过期'),
            ('unauthorized', 'API Key 无效或已过期'),
            ('401 Unauthorized', 'API Key 无效或已过期'),
            ('404 NOT FOUND', '模型 deepseek-chat 不存在'),
            ('Request timed out', '请求超时，请检查网络'),
            ('TIMEOUT', '请求超时，请检查网络'),
            ('InvalidParameter: temperature', '验证失败: InvalidParameter: temperature'),
        )
        for error, expected in cases:
            with self.subTest(error=error):
                create_model.side_effect = RuntimeError(error)
                result = asyncio.run(model.validate_and_switch_model(SimpleNamespace(
                    model_merchant='deepseek', model_name='deepseek-chat',
                )))
                self.assertFalse(result.status)
                self.assertEqual(result.info, expected)


class DatabaseInitializationTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.cursor = self.db.cursor.return_value
        self.cursor.__enter__.return_value = self.cursor
        self.driver = SimpleNamespace(connect=MagicMock(return_value=self.db), MySQLError=RuntimeError)
        config = load_module(ROOT / 'backend/config/cfg.py', {}).configer
        self.initialize = load_module(ROOT / 'backend/database_config/init_mysql.py', {
            'pymysql': self.driver,
            'config': SimpleNamespace(configer=config),
            'log_config': SimpleNamespace(logger=LOGGER),
        }).init_db

    def test_startup_still_clears_history_and_summary(self):
        self.assertIs(self.initialize(), self.db)
        queries = [call.args[0].lower() for call in self.cursor.execute.call_args_list]
        self.assertIn('delete from history_messages;', queries)
        self.assertIn('delete from abstract_messages;', queries)
        self.assertIn("insert into abstract_messages (abstract) values ('');", queries)
        self.assertEqual(self.db.commit.call_count, 2)

    def test_initialization_failure_rolls_back_before_next_table(self):
        self.cursor.execute.side_effect = [None, RuntimeError('清空历史失败'), None, None, None]
        self.assertIs(self.initialize(), self.db)
        self.db.rollback.assert_called_once()
        self.db.commit.assert_called_once()

    def test_temporary_connection_closes_if_database_creation_fails(self):
        self.driver.connect.side_effect = [RuntimeError(1049, '数据库不存在'), self.db]
        self.cursor.execute.side_effect = RuntimeError('创建数据库失败')
        with self.assertRaisesRegex(RuntimeError, '创建数据库失败'):
            self.initialize()
        self.db.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
