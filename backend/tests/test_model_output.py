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

    def test_history_is_formatted_as_natural_dialogue_and_injected_into_answer_prompt(self):
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
        reply_agent = Mock()
        reply_agent.invoke.return_value = {'messages': [SimpleNamespace(
            content='{"reply":["今天忙坏了吧","回去赶紧歇会"]}',
            usage_metadata={'total_tokens': 6},
        )]}
        nodes.generate(nodes.start_node(state, memory, ()), memory, (None, reply_agent))
        answer_prompt = reply_agent.invoke.call_args.args[0]['messages'][0]['content']
        self.assertIn('对方：刚下班\n小晚：辛苦啦', answer_prompt)
        self.assertIn('默认只回 1 到 2 条短气泡', answer_prompt)

    def test_stream_parser_emits_bubbles_incrementally_and_skips_think_blocks(self):
        p = parser.ReplyBubbleStreamParser()
        chunks = [
            '<think>先想一下', ' {"reply":["不要发出去"]}</think>\n',
            '{"reply":["第一', '条\\n带换行"', ', "第二\\"句\\""', ', "   "', ']}',
        ]
        seen = []
        for piece in chunks:
            seen.append(p.feed(piece))
        self.assertEqual(seen, [[], [], [], ['第一条\n带换行'], ['第二"句"'], [], []])
        self.assertEqual(p.emitted, ['第一条\n带换行', '第二"句"'])

    def test_generate_streams_bubbles_before_completion(self):
        from app.chat.state import create_initial_state

        state = create_initial_state()
        state['user_input'] = '今天下雨了'
        memory = SimpleNamespace(history_cache=[], abstract_cache='', has_older_history=False, next_round=0,
                                 refresh_if_needed=lambda: None)

        class StreamingReplyAgent:
            def stream(self, payload, config=None, stream_mode=None):
                self.payload = payload
                yield (SimpleNamespace(type='AIMessageChunk', content='{"reply":["我也最爱'), {})
                yield (SimpleNamespace(type='AIMessageChunk', content='下雨天宅着","没啥事'), {})
                yield (SimpleNamespace(
                    type='AIMessageChunk', content='就别出去了呗"]}',
                    usage_metadata={'total_tokens': 19},
                    response_metadata={'finish_reason': 'stop'},
                ), {})

        streamed = []
        result = nodes.generate(
            nodes.start_node(state, memory, ()),
            memory,
            (None, StreamingReplyAgent()),
            on_bubble=streamed.append,
        )
        self.assertEqual(streamed, ['我也最爱下雨天宅着', '没啥事就别出去了呗'])
        self.assertEqual(result['ai_response'], streamed)
        self.assertEqual(result['total_tokens'], 19)

    def test_generate_streams_smoothly_across_tool_call_boundary(self):
        from app.chat.state import create_initial_state

        state = create_initial_state()
        state['user_input'] = '上次说的日料店叫啥'
        memory = SimpleNamespace(history_cache=[], abstract_cache='', has_older_history=False, next_round=0,
                                 refresh_if_needed=lambda: None)

        class ToolStreamingAgent:
            def stream(self, payload, config=None, stream_mode=None):
                yield (SimpleNamespace(type='AIMessageChunk', content='{"reply":["等我翻翻哈"]}'), {})
                yield (SimpleNamespace(type='ToolMessage', content='找到以下早期对话片段：山葵屋'), {})
                yield (SimpleNamespace(type='AIMessageChunk', content='{"reply":["找到啦，叫山葵屋",'), {})
                yield (SimpleNamespace(
                    type='AIMessageChunk', content='"他家鳗鱼饭超绝那家"]}',
                    usage_metadata={'total_tokens': 32},
                    response_metadata={'finish_reason': 'stop'},
                ), {})

        streamed = []
        result = nodes.generate(
            nodes.start_node(state, memory, ()),
            memory,
            (None, ToolStreamingAgent()),
            on_bubble=streamed.append,
        )
        self.assertEqual(streamed, ['等我翻翻哈', '找到啦，叫山葵屋', '他家鳗鱼饭超绝那家'])
        self.assertEqual(result['ai_response'], streamed)
        self.assertEqual(result['total_tokens'], 32)

    def test_generate_recovers_when_json_mode_returns_empty_text_after_tool_call(self):
        from app.chat.state import create_initial_state

        state = create_initial_state()
        state['user_input'] = 'https://news.example.com/1'
        memory = SimpleNamespace(history_cache=[], abstract_cache='', has_older_history=False, next_round=0,
                                 refresh_if_needed=lambda: None)

        class FlakyPostToolAgent:
            def __init__(self):
                self.calls = 0

            def stream(self, payload, config=None, stream_mode=None):
                self.calls += 1
                if self.calls == 1:
                    yield ('messages', (SimpleNamespace(type='ToolMessage', content='链接正文...'), {}))
                    yield ('messages', (SimpleNamespace(
                        type='AIMessageChunk', content='',
                        usage_metadata={'total_tokens': 2945},
                        response_metadata={'finish_reason': 'stop'},
                    ), {}))
                    yield ('values', {'messages': payload['messages'] + [
                        SimpleNamespace(type='tool', content='链接正文...'),
                    ]})
                else:
                    yield ('messages', (SimpleNamespace(
                        type='AIMessageChunk', content='{"reply":["这篇讲的是大额存单门槛下调的事"]}',
                        usage_metadata={'total_tokens': 2990},
                        response_metadata={'finish_reason': 'stop'},
                    ), {}))

        streamed = []
        agent = FlakyPostToolAgent()
        result = nodes.generate(
            nodes.start_node(state, memory, ()),
            memory,
            (None, agent),
            on_bubble=streamed.append,
        )
        self.assertEqual(agent.calls, 2)
        self.assertEqual(streamed, ['这篇讲的是大额存单门槛下调的事'])
        self.assertEqual(result['ai_response'], streamed)

    def test_generate_recovers_when_vision_json_mode_returns_empty_text_without_tools(self):
        from app.chat.state import create_initial_state

        state = create_initial_state()
        state['user_input'] = ''
        state['turn_images'] = ['data:image/webp;base64,UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAfQ//73v/+BiOh/AAA=']
        memory = SimpleNamespace(history_cache=[], abstract_cache='', has_older_history=False, next_round=0,
                                 refresh_if_needed=lambda: None)

        class FlakyVisionAgent:
            def __init__(self):
                self.payloads = []

            def stream(self, payload, config=None, stream_mode=None):
                self.payloads.append(payload)
                if len(self.payloads) == 1:
                    # 模拟推理模型消耗推理 Token 后直接产出空正文并 stop
                    yield ('messages', (SimpleNamespace(
                        type='AIMessageChunk', content='',
                        usage_metadata={'total_tokens': 3224},
                        response_metadata={'finish_reason': 'stop'},
                    ), {}))
                else:
                    yield ('messages', (SimpleNamespace(
                        type='AIMessageChunk', content='{"reply":["这个圆环把塔正好框在中间，构图太绝了"]}',
                        usage_metadata={'total_tokens': 3480},
                        response_metadata={'finish_reason': 'stop'},
                    ), {}))

        streamed = []
        agent = FlakyVisionAgent()
        result = nodes.generate(
            nodes.start_node(state, memory, ()),
            memory,
            (None, agent),
            on_bubble=streamed.append,
        )
        self.assertEqual(len(agent.payloads), 2)
        retry_content = agent.payloads[1]['messages'][0]['content']
        self.assertEqual(retry_content[0]['type'], 'image_url')
        self.assertIn('不要返回空内容', retry_content[1]['text'])
        self.assertEqual(streamed, ['这个圆环把塔正好框在中间，构图太绝了'])
        self.assertEqual(result['ai_response'], streamed)
        self.assertEqual(result['total_tokens'], 3224 + 3480)

    def test_generate_recovers_from_graph_recursion_error_and_strips_dangling_tool_calls(self):
        from app.chat.state import create_initial_state

        class GraphRecursionError(RuntimeError):
            pass

        state = create_initial_state()
        state['user_input'] = '帮我查下今天杭州天气和新闻'
        memory = SimpleNamespace(history_cache=[], abstract_cache='', has_older_history=False, next_round=0,
                                 refresh_if_needed=lambda: None)

        class MultiToolRecursionAgent:
            def __init__(self):
                self.calls = []

            def stream(self, payload, config=None, stream_mode=None):
                self.calls.append((payload, config))
                if len(self.calls) == 1:
                    self.first_limit = config['recursion_limit']
                    yield ('messages', (SimpleNamespace(type='ToolMessage', content='1. 杭州今日多云 16~22°C'), {}))
                    yield ('values', {'messages': payload['messages'] + [
                        SimpleNamespace(type='ai', content='', tool_calls=[{'id': 'call_ok', 'name': 'web_search'}]),
                        SimpleNamespace(type='tool', tool_call_id='call_ok', content='1. 杭州今日多云 16~22°C'),
                        # 模拟步数耗尽时残留未配对 ToolMessage 的尾随 tool_calls，补救前应自动剥离避免 400
                        SimpleNamespace(type='ai', content='', tool_calls=[{'id': 'call_dangling', 'name': 'read_url'}]),
                    ]})
                    raise GraphRecursionError('Recursion limit reached')
                yield ('messages', (SimpleNamespace(
                    type='AIMessageChunk', content='{"reply":["杭州今天多云，16到22度挺舒服的"]}',
                    usage_metadata={'total_tokens': 512},
                    response_metadata={'finish_reason': 'stop'},
                ), {}))

        streamed = []
        agent = MultiToolRecursionAgent()
        result = nodes.generate(
            nodes.start_node(state, memory, ()),
            memory,
            (None, agent),
            on_bubble=streamed.append,
        )
        self.assertGreaterEqual(agent.first_limit, 20)
        self.assertEqual(len(agent.calls), 2)
        recovery_msgs = agent.calls[1][0]['messages']
        # 确认已完成的工具结果保留，而未配对的悬空 tool_calls 已被剥离
        self.assertTrue(any(getattr(m, 'tool_call_id', None) == 'call_ok' for m in recovery_msgs))
        self.assertFalse(any(
            getattr(m, 'type', None) == 'ai'
            and any(tc.get('id') == 'call_dangling' for tc in (getattr(m, 'tool_calls', None) or ()))
            for m in recovery_msgs
        ))
        self.assertEqual(streamed, ['杭州今天多云，16到22度挺舒服的'])
        self.assertEqual(result['ai_response'], streamed)
