import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import _imports
from app.chat import nodes
from app.llm import output_parser as parser


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

    def test_parse_ignores_reasoning_and_finds_expected_fields_after_other_braces(self):
        content = ('<think>这里是内部推理 {"intent":"猜测"}</think>'
                   '格式示例 {"example":true}\n实际结果：{"intent":"问候","emotion":"平静"}')
        self.assertEqual(parser.safe_parse_json(content, expected_keys=('intent', 'emotion')),
                         {'intent': '问候', 'emotion': '平静'})

    def test_deeply_nested_output_uses_fallback(self):
        with self.assertLogs(parser.logger, level='WARNING'):
            nested = '[' * 10_000 + '0'
            output = parser.safe_parse_json('{"reply":' + nested + '}', {'reply': ['fallback']})
        self.assertEqual(output, {'reply': ['fallback']})

    @staticmethod
    # 用可控的模型回复进入真实生成节点，检查解析与降级。
    def generate(content, *, finish_reason='stop'):
        from app.chat.state import create_initial_state

        state = create_initial_state()
        state['user_input'] = '测试消息'
        state['total_tokens'] = 23
        memory = SimpleNamespace(history_cache=[], abstract_cache='', has_older_history=False, next_round=1,
                                 refresh_if_needed=lambda: None)
        agent = Mock()
        message = SimpleNamespace(content=content, usage_metadata={'total_tokens': 7},
                                  response_metadata={'finish_reason': finish_reason})
        agent.invoke.return_value = {'messages': [message]}

        result = nodes.generate(nodes.start_node(state, memory, ()), memory, (None, None, agent))
        return result, agent

    def test_reply_generation_requests_natural_segments_and_keeps_usable_messages(self):
        content = json.dumps({'reply': ['第一条', '  ', '第二条\n保留换行']})
        result, agent = self.generate(content)
        self.assertEqual(result['ai_response'], ['第一条', '第二条\n保留换行'])
        self.assertEqual(result['message']['assistant'], result['ai_response'])
        self.assertEqual(result['total_tokens'], 7)
        prompt = agent.invoke.call_args.args[0]['messages'][0]['content']
        self.assertRegex(prompt, r'1\s*至\s*5\s*条')
        schema_example = json.loads(prompt.splitlines()[-1])
        self.assertEqual(set(schema_example), {'reply'})
        self.assertIsInstance(schema_example['reply'], list)
        self.assertTrue(schema_example['reply'])
        self.assertTrue(all(isinstance(part, str) and part.strip() for part in schema_example['reply']))

    def test_reply_generation_keeps_visible_plain_text_without_exposing_reasoning(self):
        with self.assertNoLogs(parser.logger, level='WARNING'):
            result, agent = self.generate('<think>内部推理</think>\n这次可以继续聊。')
        self.assertEqual(result['ai_response'], ['这次可以继续聊。'])
        agent.invoke.assert_called_once()

    def test_reply_generation_repairs_incomplete_unicode_without_losing_visible_text(self):
        for content in ('{"reply":["hello\\ud800 world","\\ud83d\\ude42"]}', 'hello\ud800 world'):
            with self.subTest(content=repr(content)):
                result, _ = self.generate(content)
                replies = result['ai_response']
                self.assertEqual(replies[0], 'hello\ufffd world')
                self.assertEqual(json.loads(json.dumps(replies, ensure_ascii=False).encode('utf-8')), replies)
                if len(replies) > 1:
                    self.assertEqual(replies[1], '\U0001f642')

    def test_reply_generation_logs_stage_when_json_is_incomplete(self):
        with self.assertLogs(parser.logger, level='WARNING') as logs:
            result, agent = self.generate('结果：{"reply":["私密回复"', finish_reason='length')
        self.assertEqual(result['ai_response'], [nodes.DEFAULT_REPLY])
        self.assertIn('回复生成', logs.output[0])
        self.assertIn('truncated_output', logs.output[0])
        self.assertNotIn('私密回复', logs.output[0])
        agent.invoke.assert_called_once()

    def test_reply_generation_drops_unclosed_reasoning(self):
        with self.assertLogs(parser.logger, level='WARNING') as logs:
            result, _ = self.generate('<think>未结束的推理')
        self.assertEqual(result['ai_response'], [nodes.DEFAULT_REPLY])
        self.assertIn('empty_visible_text', logs.output[0])
        self.assertNotIn('未结束的推理', logs.output[0])

    def test_history_is_formatted_as_natural_dialogue_and_analysis_carries_pace(self):
        from app.chat.memory import format_history_dialogue
        from app.chat.state import create_initial_state

        rendered = format_history_dialogue([
            {'conversation_round': 0, 'user': '今天下雨了', 'assistant': ['我也喜欢雨天', '出门记得带伞'], 'created_at': 1764150000000},
        ], bot_name='小晚')
        self.assertEqual(rendered, '对方：今天下雨了\n小晚：我也喜欢雨天\n小晚：出门记得带伞')
        self.assertNotIn('conversation_round', rendered)
        self.assertEqual(format_history_dialogue([]), '无')

        state = create_initial_state()
        state['user_input'] = '好累啊'
        memory = SimpleNamespace(
            history_cache=[{'conversation_round': 0, 'user': '刚下班', 'assistant': ['辛苦啦']}],
            abstract_cache='',
            has_older_history=False,
            next_round=1,
            refresh_if_needed=lambda: None,
        )
        analysis_agent = Mock()
        analysis_agent.invoke.return_value = {'messages': [SimpleNamespace(
            content='{"intent":"倾诉疲惫","emotion":"累","pace":"展开","answer":"先接住对方的累"}',
            usage_metadata={'total_tokens': 5},
        )]}
        reply_agent = Mock()
        reply_agent.invoke.return_value = {'messages': [SimpleNamespace(
            content='{"reply":["今天忙坏了吧","回去赶紧歇会"]}',
            usage_metadata={'total_tokens': 6},
        )]}
        analyzed = nodes.analysis(nodes.start_node(state, memory, ()), memory, (analysis_agent, None, reply_agent))
        self.assertEqual(analyzed['analysis_result']['pace'], '展开')
        nodes.generate(analyzed, memory, (analysis_agent, None, reply_agent))
        answer_prompt = reply_agent.invoke.call_args.args[0]['messages'][0]['content']
        self.assertIn('建议篇幅：展开', answer_prompt)
        self.assertIn('对方：刚下班\n小晚：辛苦啦', answer_prompt)
