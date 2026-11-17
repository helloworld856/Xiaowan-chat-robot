import importlib.util
import sys
import types
import unittest
from unittest.mock import patch

from _imports import BACKEND_ROOT as ROOT


def load_file(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModelSettingsTests(unittest.TestCase):
    def test_validation_clients_have_network_timeout_and_limited_retries(self):
        def client(**options):
            return options
        logger = types.SimpleNamespace(info=lambda _: None, error=lambda _: None)
        persona = types.SimpleNamespace(FRIEND_PERSONA='persona', BOT_NAME='小晚')
        with patch.dict(sys.modules, {
            'langchain': types.ModuleType('langchain'),
            'langchain.agents': types.SimpleNamespace(create_agent=lambda **kwargs: kwargs),
            'app.llm.persona': types.SimpleNamespace(ANALYSIS_PERSONA='analysis', TEXT_COMPRESS_PERSONA='compress', persona=persona),
            'app.account_logging': types.SimpleNamespace(logger=logger),
            'langchain_openai': types.SimpleNamespace(ChatOpenAI=client),
            'langchain_community.chat_models': types.SimpleNamespace(ChatTongyi=client),
        }):
            factory = load_file('tested_factory', 'agent_config/agent_factory.py')
            openai = factory.create_model('deepseek', 'test-placeholder', 'M', 0.1, validation_timeout=8)
            native = factory.create_model('tongyi', 'test-placeholder', 'M', 0.1, max_tokens=10,
                                          validation_timeout=8, official_only=True)
            custom = factory.create_agents('tongyi', 'test-placeholder', 'Custom-V2', 'https://example.com/v1')
        self.assertEqual(openai['timeout'], 8)
        self.assertEqual(openai['max_retries'], 0)
        self.assertEqual(openai['default_headers']['Accept-Encoding'], 'gzip, deflate')
        self.assertEqual(native['max_retries'], 1)
        self.assertEqual(native['model_kwargs']['request_timeout'], 8)
        self.assertEqual(native['model_kwargs']['temperature'], 0.1)
        self.assertEqual(native['model_kwargs']['max_tokens'], 10)
        self.assertEqual(native['model_kwargs']['base_address'], 'https://dashscope.aliyuncs.com/api/v1')
        self.assertEqual(len(custom), 3)
        for agent in custom:
            self.assertEqual(agent['model']['base_url'], 'https://example.com/v1')
            self.assertEqual(agent['model']['model'], 'Custom-V2')
