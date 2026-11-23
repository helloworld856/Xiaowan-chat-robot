import json
import sys
import types
import unittest
from unittest.mock import patch

import _imports
import httpx
from app.llm import factory


class ModelSettingsTests(unittest.TestCase):
    def test_official_deepseek_reply_only_requests_json_output(self):
        with patch.dict(sys.modules, {
            'langchain_openai': types.SimpleNamespace(ChatOpenAI=lambda **options: options),
        }), patch.object(factory, 'create_agent', side_effect=lambda **kwargs: kwargs):
            for base_url in ('https://api.deepseek.com', 'https://api.deepseek.com/v1/'):
                with self.subTest(base_url=base_url):
                    analysis, summary, reply = factory.create_agents(
                        'deepseek', 'test-placeholder', 'deepseek-v4-flash', base_url=base_url, adapter='openai',
                    )
                    self.assertNotIn('extra_body', analysis['model'])
                    self.assertNotIn('extra_body', summary['model'])
                    self.assertEqual(reply['model']['extra_body']['response_format'], {'type': 'json_object'})
                    self.assertIs(reply['tools'], factory.CHAT_TOOLS)

            custom = factory.create_agents('deepseek', 'test-placeholder', 'custom',
                                           base_url='https://example.com/v1', adapter='openai')
            self.assertNotIn('extra_body', custom[2]['model'])

    def test_deepseek_sends_json_mode_alongside_existing_tools(self):
        requests = []

        def complete(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                'id': 'test-completion', 'created': 1, 'object': 'chat.completion', 'model': 'deepseek-v4-flash',
                'choices': [{'index': 0, 'finish_reason': 'stop',
                             'message': {'role': 'assistant', 'content': '{"reply":["测试回复"]}'}}],
            })

        with httpx.Client(transport=httpx.MockTransport(complete), trust_env=False) as client:
            clients = types.SimpleNamespace(Client=lambda **_: client, AsyncClient=httpx.AsyncClient)
            with patch.object(factory, 'httpx', clients):
                _, _, reply = factory.create_agents('deepseek', 'test-placeholder', 'deepseek-v4-flash',
                                                    base_url='https://api.deepseek.com', adapter='openai')
            result = reply.invoke({'messages': [{'role': 'user', 'content': '返回一条 JSON 消息。'}]})

        request, = requests
        self.assertEqual(request['response_format'], {'type': 'json_object'})
        self.assertIn('get_current_time', {tool['function']['name'] for tool in request['tools']})
        self.assertEqual(json.loads(result['messages'][-1].content), {'reply': ['测试回复']})

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
        # 模拟聊天在模型切换前已经拿到一组代理。
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
            openai = factory.create_model('deepseek', 'test-placeholder', 'M', 0.1, validation_timeout=8,
                                          base_url='https://api.deepseek.com', adapter='openai')
            native = factory.create_model('tongyi', 'test-placeholder', 'M', 0.1, max_tokens=10,
                                          validation_timeout=8, base_url='https://dashscope.aliyuncs.com/api/v1',
                                          adapter='dashscope')
            custom = factory.create_agents('tongyi', 'test-placeholder', 'Custom-V2',
                                           base_url='https://example.com/v1', adapter='openai')
        self.assertEqual(openai['timeout'], 8)
        self.assertEqual(openai['max_retries'], 0)
        self.assertFalse(openai['http_client'].follow_redirects)
        self.assertFalse(openai['http_async_client'].follow_redirects)
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
            self.assertFalse(agent['model']['http_client'].follow_redirects)
            self.assertFalse(agent['model']['http_async_client'].follow_redirects)
