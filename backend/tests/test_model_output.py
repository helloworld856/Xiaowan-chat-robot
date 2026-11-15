import importlib.util
import sys
import unittest
from pathlib import Path

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
