import sys
import types
import unittest
from unittest.mock import patch

import _imports
from app.llm import factory


class ModelSettingsTests(unittest.TestCase):
    def test_admin_model_switch_leaves_other_runtime_and_existing_snapshot_unchanged(self):
        from app.llm.admin_settings import ModelSettings
        from app.llm.configuration import AdminModelRuntime

        active = AdminModelRuntime()
        other = AdminModelRuntime()
        previous = ModelSettings(model_merchant='deepseek', model_name='previous')
        replacement = ModelSettings(model_merchant='deepseek', model_name='replacement')
        first_agents = (object(), object(), object())
        next_agents = (object(), object(), object())
        active.publish(previous, first_agents)
        in_flight = active.snapshot()

        active.publish(replacement, next_agents)

        self.assertEqual(in_flight, (previous, first_agents))
        self.assertEqual(active.snapshot(), (replacement, next_agents))
        self.assertIsNone(other.snapshot())

    def test_validation_clients_have_network_timeout_and_limited_retries(self):
        def client(**options):
            return options
        with patch.dict(sys.modules, {
            'langchain_openai': types.SimpleNamespace(ChatOpenAI=client),
            'langchain_community.chat_models': types.SimpleNamespace(ChatTongyi=client),
        }), patch.object(factory, 'create_agent', side_effect=lambda **kwargs: kwargs):
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
