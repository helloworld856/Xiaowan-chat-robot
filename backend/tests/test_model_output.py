import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
spec = importlib.util.spec_from_file_location('tested_json_parser', BACKEND / 'utils' / 'json_parser.py')
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


class ModelOutputTests(unittest.TestCase):
    def test_structured_content_extracts_only_visible_text(self):
        content = [
            {'type': 'reasoning', 'reasoning': '{"reply": ["hidden"]}'},
            {'type': 'text', 'text': '{"reply":'},
            {'type': 'text', 'text': '["hello"]}'},
        ]
        self.assertEqual(parser.safe_parse_json(content), {'reply': ['hello']})

    def test_malformed_reply_is_not_logged(self):
        with self.assertLogs(parser.logger, level='WARNING') as logs:
            output = parser.safe_parse_json('private chat text', {'reply': ['fallback']})
        self.assertEqual(output, {'reply': ['fallback']})
        self.assertNotIn('private chat text', '\n'.join(logs.output))

    def test_deeply_nested_output_uses_fallback(self):
        with self.assertLogs(parser.logger, level='WARNING'):
            nested = '[' * 10_000 + '0'
            output = parser.safe_parse_json('{"reply":' + nested + '}', {'reply': ['fallback']})
        self.assertEqual(output, {'reply': ['fallback']})

    def test_reply_generation_requests_natural_segments_and_keeps_usable_messages(self):
        from state_config import create_initial_state

        spec = importlib.util.spec_from_file_location('tested_reply_nodes', BACKEND / 'graph_config' / 'nodes.py')
        nodes = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(nodes)

        state = create_initial_state()
        state['user_input'] = '测试消息'
        state['total_tokens'] = 23
        memory = SimpleNamespace(history_cache=[], abstract_cache='', next_round=1, refresh_if_needed=lambda: None)
        content = json.dumps({'reply': ['第一条', '  ', '第二条\n保留换行']})
        agent = Mock()
        agent.invoke.return_value = {'messages': [SimpleNamespace(content=content, usage_metadata={'total_tokens': 7})]}

        result = nodes.generate(nodes.start_node(state, memory, ()), memory, (None, None, agent))
        self.assertEqual(result['ai_response'], ['第一条', '第二条\n保留换行'])
        self.assertEqual(result['message']['assistant'], result['ai_response'])
        self.assertEqual(result['total_tokens'], 7)
        prompt = agent.invoke.call_args.args[0]['messages'][0]['content']
        self.assertIn('1 至 5 条', prompt)
