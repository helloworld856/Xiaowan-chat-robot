import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_file(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModelPreferencesTests(unittest.TestCase):
    def test_validation_clients_have_network_timeout_and_limited_retries(self):
        def client(**options):
            return options
        logger = types.SimpleNamespace(info=lambda _: None, error=lambda _: None)
        persona = types.SimpleNamespace(FRIEND_PERSONA='persona', BOT_NAME='小晚')
        with patch.dict(sys.modules, {
            'langchain': types.ModuleType('langchain'),
            'langchain.agents': types.SimpleNamespace(create_agent=lambda **kwargs: kwargs),
            'persona_config': types.SimpleNamespace(ANALYSIS_PERSONA='analysis', TEXT_COMPRESS_PERSONA='compress', persona=persona),
            'log_config': types.SimpleNamespace(logger=logger),
            'langchain_openai': types.SimpleNamespace(ChatOpenAI=client),
            'langchain_community.chat_models': types.SimpleNamespace(ChatTongyi=client),
        }):
            factory = load_file('tested_factory', 'agent_config/agent_factory.py')
            openai = factory.create_model('deepseek', 'test-placeholder', 'M', 0.1, validation_timeout=8)
            native = factory.create_model('tongyi', 'test-placeholder', 'M', 0.1, validation_timeout=8)
            custom = factory.create_agents('tongyi', 'test-placeholder', 'Custom-V2', 'https://example.com/v1')
        self.assertEqual(openai['timeout'], 8)
        self.assertEqual(openai['max_retries'], 0)
        self.assertEqual(native['max_retries'], 1)
        self.assertEqual(native['model_kwargs']['request_timeout'], 8)
        self.assertEqual(len(custom), 3)
        for agent in custom:
            self.assertEqual(agent['model']['base_url'], 'https://example.com/v1')
            self.assertEqual(agent['model']['model'], 'Custom-V2')

    def test_generate_applies_saved_preferences_to_prompt_and_output(self):
        from settings_config import AppSettings, Preferences
        prompts = []
        def invoke(request):
            prompts.append(request['messages'][0]['content'])
            return {'messages': [types.SimpleNamespace(content=json.dumps({'reply': ['你好', '今天如何']}))]}
        logger = types.SimpleNamespace(info=lambda _: None, error=lambda _: None)
        config = types.SimpleNamespace(GENERATE_DEFAULT_RESULT={'reply': ['fallback']})
        with patch.dict(sys.modules, {
            'agent_config': types.SimpleNamespace(friend_agent=types.SimpleNamespace(invoke=invoke)),
            'config': types.SimpleNamespace(configer=config),
            'log_config': types.SimpleNamespace(logger=logger),
            'memory_config': types.SimpleNamespace(memorier=types.SimpleNamespace(abstract_cache='', history_cache=[])),
            'persona_config': types.SimpleNamespace(persona=types.SimpleNamespace(BOT_NAME='小晚')),
            'utils': types.SimpleNamespace(count_tokens=lambda _: 0, safe_parse_json=lambda value, _: json.loads(value)),
        }):
            nodes = load_file('tested_nodes', 'graph_config/nodes.py')
        saved = AppSettings(preferences=Preferences(reply_length='short', split_reply=False))
        with patch.object(nodes.settings_store, 'get', return_value=saved):
            state = nodes.generate({'user_input': '你好', 'analysis_result': {}, 'total_tokens': 0, 'message': {}})
        self.assertIn('简短', prompts[0])
        self.assertIn('只回复一条', prompts[0])
        self.assertEqual(state['ai_response'], ['你好\n\n今天如何'])
        self.assertEqual(state['message']['assistant'], ['你好\n\n今天如何'])
