import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import _imports
import requests
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from tools_config.registry import CHAT_TOOLS


class ChatToolTests(unittest.TestCase):
    def setUp(self):
        self.tools = {tool.name: tool for tool in CHAT_TOOLS}

    def test_model_receives_explicit_function_and_parameter_descriptions(self):
        self.assertEqual(
            set(self.tools),
            {'get_weather', 'get_current_time', 'search_chat_history', 'web_search', 'read_url'},
        )
        for name, parameter, required, expected_type in (
            ('get_weather', 'city', ['city'], 'string'),
            ('get_current_time', 'timezone', [], 'string'),
            ('search_chat_history', 'keywords', [], 'array'),
            ('web_search', 'query', ['query'], 'string'),
            ('read_url', 'url', ['url'], 'string'),
        ):
            with self.subTest(tool=name):
                tool = convert_to_openai_tool(self.tools[name])
                self.assertEqual(tool['type'], 'function')
                function = tool['function']
                self.assertEqual(function['name'], name)
                self.assertTrue(function['description'])
                schema = function['parameters']
                self.assertEqual(schema['type'], 'object')
                self.assertEqual(schema['required'], required)
                self.assertEqual(schema['properties'][parameter]['type'], expected_type)
                self.assertTrue(schema['properties'][parameter]['description'])
        time_schema = convert_to_openai_tool(self.tools['get_current_time'])['function']['parameters']
        self.assertEqual(time_schema['properties']['timezone']['default'], 'Asia/Shanghai')
        self.assertEqual(time_schema['properties']['operation']['enum'],
                         ['now', 'add_days', 'days_between', 'convert_timezone'])

    def test_registered_time_uses_beijing_timezone_when_omitted(self):
        with patch('tools_config.time_tool.time.ZoneInfo', return_value=timezone.utc) as zone:
            result = self.tools['get_current_time'].invoke({})
        zone.assert_called_once_with('Asia/Shanghai')
        self.assertRegex(result, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$')

    def test_time_date_arithmetic_supports_leap_years_and_negative_days(self):
        tool = self.tools['get_current_time']
        for start, days, expected in (('2024-02-27', 2, '2024-02-29（星期四）'),
                                      ('2025-01-01', -1, '2024-12-31（星期二）')):
            with self.subTest(start=start, days=days):
                self.assertIn(expected, tool.invoke({'operation': 'add_days', 'start_date': start, 'days': days}))

    def test_time_date_difference_defaults_to_today_in_the_requested_zone(self):
        instant = datetime(2024, 1, 1, 17, tzinfo=timezone.utc)
        tool = self.tools['get_current_time']
        with patch('tools_config.time_tool.time.datetime', wraps=datetime) as clock:
            clock.now.side_effect = instant.astimezone
            upcoming = tool.invoke({'operation': 'days_between', 'end_date': '2024-01-05'})
            past = tool.invoke({'operation': 'days_between', 'end_date': '2023-12-31'})
            other_zone = tool.invoke({'operation': 'days_between', 'end_date': '2024-01-05', 'timezone': 'UTC'})
        self.assertIn('2024-01-02', upcoming)
        self.assertTrue(upcoming.endswith('3 天'))
        self.assertTrue(past.endswith('-2 天'))
        self.assertTrue(other_zone.endswith('4 天'))

    def test_time_zone_conversion_returns_the_destination_day_and_offset(self):
        result = self.tools['get_current_time'].invoke({
            'operation': 'convert_timezone', 'local_datetime': '2024-01-15T08:30',
            'target_timezone': 'America/New_York',
        })
        self.assertIn('2024-01-14 19:30:00-05:00', result)
        self.assertIn('America/New_York', result)

    def test_time_zone_conversion_handles_daylight_saving_gaps_and_overlaps(self):
        tool = self.tools['get_current_time']
        params = {'operation': 'convert_timezone', 'timezone': 'America/New_York',
                  'target_timezone': 'Asia/Shanghai'}
        gap = tool.invoke({**params, 'local_datetime': '2024-03-10T02:30'})
        overlap = tool.invoke({**params, 'local_datetime': '2024-11-03T01:30'})
        earlier = tool.invoke({**params, 'local_datetime': '2024-11-03T01:30-04:00'})
        later = tool.invoke({**params, 'local_datetime': '2024-11-03T01:30-05:00'})
        wrong_offset = tool.invoke({**params, 'local_datetime': '2024-11-03T01:30+00:00'})
        self.assertIn('不存在', gap)
        self.assertIn('UTC 偏移量', overlap)
        self.assertIn('2024-11-03T01:30:00-04:00', overlap)
        self.assertIn('2024-11-03T01:30:00-05:00', overlap)
        self.assertIn('2024-11-03 13:30:00+08:00', earlier)
        self.assertIn('2024-11-03 14:30:00+08:00', later)
        self.assertIn('不匹配', wrong_offset)

    def test_time_returns_clear_errors_for_invalid_arguments(self):
        tool = self.tools['get_current_time']
        for args, expected in (
            ({'timezone': 'Invalid/Unknown'}, '无效时区'),
            ({'operation': 'add_days', 'start_date': '2023-02-29', 'days': 1}, '有效日期'),
            ({'operation': 'add_days', 'start_date': '9999-12-31', 'days': 1}, '超出'),
            ({'operation': 'add_days'}, 'days'),
            ({'operation': 'days_between'}, 'end_date'),
            ({'operation': 'convert_timezone', 'target_timezone': 'UTC'}, 'local_datetime'),
            ({'operation': 'convert_timezone', 'local_datetime': '2024-01-15'}, 'target_timezone'),
        ):
            with self.subTest(args=args):
                self.assertIn(expected, tool.invoke(args))

    def test_registered_weather_executes_original_handler_and_validates_input(self):
        geocoding = Mock()
        geocoding.json.return_value = {'results': [{'name': '杭州', 'country': '中国',
                                                     'latitude': 30.2, 'longitude': 120.1}]}
        weather = Mock()
        weather.json.return_value = {'current': {'time': '2026-11-20T12:00', 'temperature_2m': 18,
                                                 'relative_humidity_2m': 60}}
        with patch('tools_config.weather_tool.weather.requests.get', side_effect=[geocoding, weather]) as request:
            with self.assertRaises(ValidationError):
                self.tools['get_weather'].invoke({})
            with self.assertRaises(ValidationError):
                self.tools['get_weather'].invoke({'city': 42})
            request.assert_not_called()
            result = self.tools['get_weather'].invoke({'city': '杭州'})
        self.assertIn('地点：杭州', result)
        self.assertIn('气温：18°C', result)
        self.assertEqual(request.call_args_list[0].kwargs['params']['name'], '杭州')

    def test_weather_distinguishes_malformed_responses_and_missing_cities(self):
        for payload, expected in (([], '天气服务暂时不可用，请稍后再试。'),
                                  ({'results': None}, '天气服务暂时不可用，请稍后再试。'),
                                  ({}, '找不到城市：杭州')):
            with self.subTest(payload=payload), patch('tools_config.weather_tool.weather.requests.get') as request:
                request.return_value.json.return_value = payload
                self.assertEqual(self.tools['get_weather'].invoke({'city': '杭州'}), expected)
                request.assert_called_once()

    def test_weather_failure_logs_stage_and_status_without_exception_text(self):
        response = requests.Response()
        response.status_code = 403
        error = requests.HTTPError('private weather exception', response=response)
        with (patch('tools_config.weather_tool.weather.requests.get', side_effect=error),
              self.assertLogs('xiaowan', level='WARNING') as recorded):
            self.assertEqual(self.tools['get_weather'].invoke({'city': '杭州'}), '天气服务暂时不可用，请稍后再试。')
        self.assertIn('event=tool_failure tool=weather stage=geocoding error=HTTPError http_status=403', recorded.output[0])
        self.assertNotIn('private weather exception', recorded.output[0])

    def test_weather_rejects_missing_or_invalid_current_values(self):
        location = Mock(json=Mock(return_value={'results': [
            {'name': '杭州', 'latitude': 30.2, 'longitude': 120.1},
        ]}))
        current = {'time': '2026-11-20T12:00', 'temperature_2m': 18, 'relative_humidity_2m': 60}
        for invalid in ({'time': None}, {'temperature_2m': None}, {'relative_humidity_2m': False}):
            conditions = Mock(json=Mock(return_value={'current': {**current, **invalid}}))
            with (self.subTest(invalid=invalid),
                  patch('tools_config.weather_tool.weather.requests.get', side_effect=[location, conditions]),
                  self.assertLogs('xiaowan', level='WARNING')):
                self.assertEqual(self.tools['get_weather'].invoke({'city': '杭州'}), '天气服务暂时不可用，请稍后再试。')

    def test_weather_omits_empty_country(self):
        location = Mock(json=Mock(return_value={'results': [
            {'name': '杭州', 'country': None, 'latitude': 30.2, 'longitude': 120.1},
        ]}))
        conditions = Mock(json=Mock(return_value={'current': {
            'time': '2026-11-20T12:00', 'temperature_2m': 18, 'relative_humidity_2m': 60,
        }}))
        with patch('tools_config.weather_tool.weather.requests.get', side_effect=[location, conditions]):
            result = self.tools['get_weather'].invoke({'city': '杭州'})
        self.assertTrue(result.startswith('地点：杭州；'))

    def _location(self):
        return Mock(json=Mock(return_value={'results': [
            {'name': '杭州', 'country': '中国', 'latitude': 30.2, 'longitude': 120.1},
        ]}))

    def test_weather_describes_conditions_and_optional_current_values(self):
        conditions = Mock(json=Mock(return_value={'current': {
            'time': '2026-11-20T12:00', 'temperature_2m': 18, 'relative_humidity_2m': 60,
            'apparent_temperature': 16.5, 'weather_code': 61, 'wind_speed_10m': 12.3, 'precipitation': 0.4,
        }}))
        with patch('tools_config.weather_tool.weather.requests.get',
                   side_effect=[self._location(), conditions]) as request:
            result = self.tools['get_weather'].invoke({'city': '杭州'})
        for expected in ('天气：小雨', '体感：16.5°C', '风速：12.3 km/h', '当前降水：0.4 mm'):
            self.assertIn(expected, result)
        self.assertNotIn('daily', request.call_args_list[1].kwargs['params'])
        self.assertNotIn('预报', result)

    def test_weather_returns_daily_forecast_when_days_requested(self):
        conditions = Mock(json=Mock(return_value={
            'current': {'time': '2026-11-20T12:00', 'temperature_2m': 18, 'relative_humidity_2m': 60},
            'daily': {
                'time': ['2026-11-20', '2026-11-21'], 'weather_code': [0, 95],
                'temperature_2m_max': [20, 17.5], 'temperature_2m_min': [11, 9],
                'precipitation_probability_max': [5, None],
            },
        }))
        with patch('tools_config.weather_tool.weather.requests.get',
                   side_effect=[self._location(), conditions]) as request:
            result = self.tools['get_weather'].invoke({'city': '杭州', 'days': 2})
        params = request.call_args_list[1].kwargs['params']
        self.assertEqual(params['forecast_days'], 2)
        self.assertIn('temperature_2m_max', params['daily'])
        self.assertIn('未来 2 天预报：2026-11-20 晴，11~20°C，降水概率 5%；2026-11-21 雷阵雨，9~17.5°C', result)
        self.assertNotIn('None', result)

    def test_weather_rejects_out_of_range_days_and_short_forecasts(self):
        with patch('tools_config.weather_tool.weather.requests.get') as request:
            self.assertIn('0 到 7', self.tools['get_weather'].invoke({'city': '杭州', 'days': 8}))
            request.assert_not_called()
        short = Mock(json=Mock(return_value={
            'current': {'time': '2026-11-20T12:00', 'temperature_2m': 18, 'relative_humidity_2m': 60},
            'daily': {'time': ['2026-11-20'], 'weather_code': [0], 'temperature_2m_max': [20],
                      'temperature_2m_min': [11]},
        }))
        with (patch('tools_config.weather_tool.weather.requests.get', side_effect=[self._location(), short]),
              self.assertLogs('xiaowan', level='WARNING')):
            self.assertEqual(self.tools['get_weather'].invoke({'city': '杭州', 'days': 3}),
                             '天气服务暂时不可用，请稍后再试。')


class SearchHistoryToolTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from test_conversation_storage import SQLitePool

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pool = SQLitePool(Path(folder.name) / 'history.sqlite')
        self.tool = {t.name: t for t in CHAT_TOOLS}['search_chat_history']

    def _insert(self, user_id: int, round_no: int, user_text: str, replies: list[str], created_at: int | None):
        import json
        with self.pool.connection() as db, db.cursor() as cur:
            cur.execute(
                'INSERT INTO history_messages (user_id, conversation_round, user, assistant, created_at) '
                'VALUES (%s, %s, %s, %s, %s)',
                (user_id, round_no, user_text, json.dumps(replies, ensure_ascii=False), created_at),
            )
            db.commit()

    def test_search_history_isolates_accounts_excludes_active_window_and_expands_neighbor_rounds(self):
        from app.chat.tool_context import ChatToolContext, chat_tool_context

        # 2026-10-15 12:00 Asia/Shanghai
        oct_15_ms = int(datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
        # 2026-11-20 12:00 Asia/Shanghai
        nov_20_ms = int(datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)

        self._insert(1, 0, '今天去城西吃了一家超棒的日料', ['叫什么名字呀'], oct_15_ms)
        self._insert(1, 1, '叫山葵屋，他家鳗鱼饭绝了', ['记下了，下次去试试'], oct_15_ms + 60_000)
        self._insert(1, 2, '最近在看三体', ['好看吗'], nov_20_ms)
        # 已经在当前短期滑动窗口内（round >= 3）的记录不应重复返回
        self._insert(1, 3, '今天又想吃日料了', ['去吃呀'], nov_20_ms + 120_000)
        # 其他账号的私密对话绝不能被搜出
        self._insert(2, 0, '秘密日料店叫深海亭', ['收到'], oct_15_ms)

        ctx = ChatToolContext(user_id=1, connection=self.pool.connection, window_start_round=3)
        with chat_tool_context(ctx):
            res = self.tool.invoke({'keywords': ['日料']})
            self.assertIn('城西吃了一家超棒的日料', res)
            # 相邻下一轮（round 1）中的店名「山葵屋」被自动补全带出
            self.assertIn('山葵屋', res)
            self.assertNotIn('今天又想吃日料了', res)
            self.assertNotIn('深海亭', res)

            by_date = self.tool.invoke({'keywords': ['三体'], 'start_date': '2026-11-01', 'end_date': '2026-11-25'})
            self.assertIn('最近在看三体', by_date)

            miss_date = self.tool.invoke({'keywords': ['三体'], 'start_date': '2026-10-01', 'end_date': '2026-10-31'})
            self.assertIn('未在早期对话记录中找到', miss_date)

            bad_date = self.tool.invoke({'keywords': ['日料'], 'start_date': '2026/10/01'})
            self.assertIn('日期格式无效', bad_date)


class ReadUrlToolTests(unittest.TestCase):
    def setUp(self):
        self.tool = {t.name: t for t in CHAT_TOOLS}['read_url']

    def test_read_url_blocks_private_addresses_and_ssrf_redirects(self):
        from tools_config.url_tool import url_reader

        for blocked in (
            'http://localhost:8000/admin',
            'http://127.0.0.1/secret',
            'https://192.168.1.10/router',
            'http://169.254.169.254/latest/meta-data/',
            'ftp://example.com/file.txt',
        ):
            with self.subTest(url=blocked):
                out = self.tool.invoke({'url': blocked})
                self.assertIn('无法读取该链接', out)

        # 公网域名 302 跳转到内网 IP 时，第二跳会被拦截
        def fake_getaddrinfo(host, port, type=0):
            ip = '93.184.216.34' if host == 'public.example.com' else '10.0.0.8'
            return [(2, 1, 6, '', (ip, port))]

        def redirect_handler(request: 'httpx.Request'):
            import httpx
            return httpx.Response(302, headers={'Location': 'http://internal.corp.local/secret'})

        import httpx
        mock_client = httpx.Client(transport=httpx.MockTransport(redirect_handler))
        with (
            patch.object(url_reader.socket, 'getaddrinfo', side_effect=fake_getaddrinfo),
            patch.object(url_reader, '_fetch_single_hop', return_value=mock_client),
        ):
            out = self.tool.invoke({'url': 'https://public.example.com/short'})
        self.assertIn('无法读取该链接', out)

    def test_read_url_extracts_clean_article_and_rejects_binary_content(self):
        import httpx
        from tools_config.url_tool import url_reader

        html = (
            '<html><head><title>  苏式汤面指南 </title>'
            '<meta name="description" content="一碗好面的汤底秘诀">'
            '<style>.ad{display:none}</style><script>alert(1)</script></head>'
            '<body><nav>导航栏噪音</nav><main><h1>红汤与白汤</h1><p>焖肉面要趁热吃。</p></main>'
            '<footer>版权所有</footer></body></html>'
        ).encode('utf-8')

        def html_handler(request: httpx.Request):
            return httpx.Response(200, headers={'Content-Type': 'text/html; charset=utf-8'}, content=html)

        def pdf_handler(request: httpx.Request):
            return httpx.Response(200, headers={'Content-Type': 'application/pdf'}, content=b'%PDF-1.7')

        with (
            patch.object(url_reader.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]),
            patch.object(url_reader, '_fetch_single_hop',
                         side_effect=lambda *_: httpx.Client(transport=httpx.MockTransport(html_handler))),
        ):
            result = self.tool.invoke({'url': 'https://example.com/noodles'})
        self.assertIn('标题：苏式汤面指南', result)
        self.assertIn('简介：一碗好面的汤底秘诀', result)
        self.assertIn('焖肉面要趁热吃。', result)
        self.assertNotIn('导航栏噪音', result)
        self.assertNotIn('alert(1)', result)

        with (
            patch.object(url_reader.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]),
            patch.object(url_reader, '_fetch_single_hop',
                         side_effect=lambda *_: httpx.Client(transport=httpx.MockTransport(pdf_handler))),
        ):
            pdf_res = self.tool.invoke({'url': 'https://example.com/doc.pdf'})
        self.assertIn('application/pdf', pdf_res)

        # 门户网站把新闻正文放在 JS 变量（如 var contentdate = '<p>...</p>'）时也能顺利提取正文
        portal_html = (
            '<html><head><title>新闻详情</title></head><body><div id="text_area">正在加载</div>'
            '<script type="text/javascript">'
            "var contentdate = '<p>第一段：个人认购起点下调至10万元。</p><p>第二段：进一步便利居民财富管理。</p>';"
            '</script></body></html>'
        ).encode('utf-8')
        with (
            patch.object(url_reader.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]),
            patch.object(
                url_reader, '_fetch_single_hop',
                side_effect=lambda *_: httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(200, headers={'Content-Type': 'text/html; charset=utf-8'}, content=portal_html)
                    )
                ),
            ),
        ):
            portal_res = self.tool.invoke({'url': 'https://news.example.com/article.shtml'})
        self.assertIn('个人认购起点下调至10万元', portal_res)
        self.assertIn('进一步便利居民财富管理', portal_res)
        self.assertNotIn('正在加载', portal_res)


class ToolSafetyAndSmoothingTests(unittest.TestCase):
    def test_timeout_call_cap_external_taint_and_two_phase_destructive_confirmation(self):
        import re
        import time
        from app.chat.tool_context import ChatToolContext, PendingWriteConfirmations, chat_tool_context
        from tools_config.registry import register_tool

        executed = []

        def slow_tool(query: str) -> str:
            time.sleep(0.25)
            return query

        slow = register_tool(
            {
                'type': 'function',
                'function': {
                    'name': 'slow_lookup',
                    'description': 'slow test tool',
                    'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']},
                },
            },
            slow_tool,
            risk_level='read',
            timeout_seconds=0.05,
        )
        # 1. 单工具硬超时平滑降级，不抛异常中断聊天
        self.assertIn('超时', slow.invoke({'query': 'hello'}))

        def ext_reader(url: str) -> str:
            return f'fetched:{url}'

        def add_memo(content: str) -> str:
            executed.append(('add', content))
            return f'已添加：{content}'

        def delete_memo(memo_id: int, confirm_token: str = '') -> str:
            executed.append(('delete', memo_id))
            return f'已删除：{memo_id}'

        reader_tool = register_tool(
            {
                'type': 'function',
                'function': {
                    'name': 'mock_read_url',
                    'description': 'read external url',
                    'parameters': {'type': 'object', 'properties': {'url': {'type': 'string'}}, 'required': ['url']},
                },
            },
            ext_reader,
            risk_level='read',
            taints_context=True,
            timeout_seconds=1.0,
        )
        safe_write_tool = register_tool(
            {
                'type': 'function',
                'function': {
                    'name': 'add_memo',
                    'description': 'add memo',
                    'parameters': {'type': 'object', 'properties': {'content': {'type': 'string'}}, 'required': ['content']},
                },
            },
            add_memo,
            risk_level='write_safe',
            timeout_seconds=None,
        )
        destructive_tool = register_tool(
            {
                'type': 'function',
                'function': {
                    'name': 'delete_memo',
                    'description': 'delete memo',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'memo_id': {'type': 'integer'},
                            'confirm_token': {'type': 'string', 'default': ''},
                        },
                        'required': ['memo_id'],
                    },
                },
            },
            delete_memo,
            risk_level='write_destructive',
            timeout_seconds=None,
            describe_action=lambda args: f"删除备忘 #{args['memo_id']}",
        )

        confirmations = PendingWriteConfirmations()

        # 2. 同一轮读取外部网页后（external_tainted=True），拦截一切写操作防提示词注入
        tainted_ctx = ChatToolContext(user_id=7, max_tool_calls=4, confirmations=confirmations)
        with chat_tool_context(tainted_ctx):
            self.assertEqual(reader_tool.invoke({'url': 'https://example.com'}), 'fetched:https://example.com')
            self.assertTrue(tainted_ctx.external_tainted)
            blocked_safe = safe_write_tool.invoke({'content': '被网页注入的备忘'})
            blocked_del = destructive_tool.invoke({'memo_id': 9})
        self.assertIn('安全拦截', blocked_safe)
        self.assertIn('安全拦截', blocked_del)
        self.assertEqual(executed, [])

        # 3. 破坏性写操作两阶段确认：首轮挂起返回 confirm_token，篡改参数或其他账号不可冒用，下一轮匹配确认后才执行一次
        turn1_ctx = ChatToolContext(user_id=7, confirmations=confirmations)
        with chat_tool_context(turn1_ctx):
            prompt_confirm = destructive_tool.invoke({'memo_id': 42})
        self.assertEqual(executed, [])
        self.assertIn('待确认操作', prompt_confirm)
        token_match = re.search(r'confirm_token="(cfm_[0-9a-f]+)"', prompt_confirm)
        self.assertIsNotNone(token_match)
        token = token_match.group(1)

        # 其他账号冒用同一 token 无效
        other_user_ctx = ChatToolContext(user_id=8, confirmations=confirmations)
        with chat_tool_context(other_user_ctx):
            wrong_user_res = destructive_tool.invoke({'memo_id': 42, 'confirm_token': token})
        self.assertIn('待确认操作', wrong_user_res)
        self.assertEqual(executed, [])

        # 同一账号但篡改了 memo_id 参数也无效
        tamper_ctx = ChatToolContext(user_id=7, confirmations=confirmations)
        with chat_tool_context(tamper_ctx):
            tampered_res = destructive_tool.invoke({'memo_id': 99, 'confirm_token': token})
        self.assertIn('待确认操作', tampered_res)
        self.assertEqual(executed, [])

        # 用户下一轮确认后，携带原参数与 token 成功执行，且 token 一次性失效不可重放
        turn2_ctx = ChatToolContext(user_id=7, confirmations=confirmations)
        with chat_tool_context(turn2_ctx):
            ok_res = destructive_tool.invoke({'memo_id': 42, 'confirm_token': token})
            replay_res = destructive_tool.invoke({'memo_id': 42, 'confirm_token': token})
        self.assertEqual(ok_res, '已删除：42')
        self.assertEqual(executed, [('delete', 42)])
        self.assertIn('待确认操作', replay_res)

        # 4. 单轮工具调用总次数上限防死循环
        capped_ctx = ChatToolContext(user_id=7, max_tool_calls=2, confirmations=confirmations)
        with chat_tool_context(capped_ctx):
            self.assertIn('已添加', safe_write_tool.invoke({'content': '第一条'}))
            self.assertIn('已添加', safe_write_tool.invoke({'content': '第二条'}))
            third = safe_write_tool.invoke({'content': '第三条'})
        self.assertIn('次数已达上限', third)
        self.assertEqual(len(executed), 3)

    def test_optional_tool_parameters_accept_explicit_null_from_non_deepseek_models(self):
        tools = {t.name: t for t in CHAT_TOOLS}
        # 其他厂商模型常对未使用的可选参数传 null，注册表应自动回退为默认参数而不报 ValidationError
        time_res = tools['get_current_time'].invoke({'operation': None, 'timezone': None})
        self.assertRegex(time_res, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
        hist_res = tools['search_chat_history'].invoke({'keywords': None, 'start_date': None, 'end_date': None})
        self.assertIn('请提供至少一个搜索关键词', hist_res)
        # 必填参数传 null 仍必须触发 ValidationError
        with self.assertRaises(ValidationError):
            tools['get_weather'].invoke({'city': None, 'days': None})


class SearxngWebSearchToolTests(unittest.TestCase):
    def setUp(self):
        from tools_config.search_tool.searxng import searxng_router
        self.router = searxng_router
        self.router.reset_state()
        self.addCleanup(self.router.reset_state)
        self.tool = {t.name: t for t in CHAT_TOOLS}['web_search']

    def test_auto_region_switches_engines_caches_results_and_marks_external_taint(self):
        import httpx
        from app.chat.tool_context import ChatToolContext, chat_tool_context
        from app.system_config import SearchConfig
        from tools_config.search_tool import searxng as searx_mod

        real_client = httpx.Client
        seen_requests: list[tuple[str, dict]] = []

        def fake_client(*args, **kwargs):
            def handler(request: httpx.Request):
                url_str = str(request.url)
                if 'generate_204' in url_str:
                    # 模拟国内无代理网络：Google 连通性探测不可达
                    raise httpx.ConnectTimeout('blocked')
                seen_requests.append((url_str.split('?', 1)[0], dict(request.url.params)))
                return httpx.Response(200, json={
                    'results': [
                        {
                            'title': '<b>周末档新片</b>上映排片表',
                            'url': 'https://movie.example.com/weekend/',
                            'content': '本周五共有 <em>3 部</em>动画与悬疑新片上映。',
                            'publishedDate': '2026-11-27',
                        },
                        {
                            # 重复 URL 应自动去重
                            'title': '重复条目',
                            'url': 'https://movie.example.com/weekend',
                            'content': '重复内容',
                        },
                    ],
                    'unresponsive_engines': [],
                })

            return real_client(transport=httpx.MockTransport(handler))

        cfg = SearchConfig(
            urls=['http://127.0.0.1:8080'],
            region='auto',
            cache_ttl_seconds=600,
        )
        ctx = ChatToolContext(user_id=9, search_config=cfg)
        with patch.object(searx_mod.httpx, 'Client', side_effect=fake_client), chat_tool_context(ctx):
            first = self.tool.invoke({'query': '周末新上映电影', 'time_range': 'week'})
            # 第二次相同查询应直接命中内存缓存，不再发 HTTP 请求，同时受单轮搜索次数上限保护
            second = self.tool.invoke({'query': '周末新上映电影', 'time_range': 'week'})
            third = self.tool.invoke({'query': '另一部电影', 'time_range': None})

        self.assertTrue(ctx.external_tainted)
        self.assertEqual(len(seen_requests), 1)
        endpoint, params = seen_requests[0]
        self.assertEqual(endpoint, 'http://127.0.0.1:8080/search')
        self.assertEqual(params['engines'], cfg.cn_engines)
        self.assertEqual(params['time_range'], 'week')
        self.assertEqual(params['language'], 'zh-CN')
        self.assertIn('1. 周末档新片上映排片表（2026-11-27）', first)
        self.assertIn('3 部', first)
        self.assertNotIn('<b>', first)
        self.assertNotIn('重复条目', first)
        self.assertEqual(first, second)
        self.assertIn('次数已够用', third)

    def test_multi_server_auto_classification_latency_selection_and_failover(self):
        import httpx
        from app.chat.tool_context import ChatToolContext, chat_tool_context
        from app.system_config import SearchConfig
        from tools_config.search_tool import searxng as searx_mod

        # 模拟机器处于国内网络，同时配置了国内节点（首轮故障 502）与海外节点（自动探测出支持 Google）
        real_client = httpx.Client
        search_calls: list[tuple[str, str]] = []
        cn_failed_once = {'done': False}

        def fake_client(*args, **kwargs):
            def handler(request: httpx.Request):
                host = request.url.host
                path = request.url.path
                params = dict(request.url.params)
                if host == 'www.google.com':
                    raise httpx.ConnectTimeout('domestic network')
                if path == '/healthz':
                    return httpx.Response(200, text='OK')
                if host == 'searx-hk.example.org' and params.get('q') == 'wikipedia' and params.get('engines') == 'google':
                    # 远程节点自身在海外，Google 探测正常返回结果
                    return httpx.Response(200, json={
                        'results': [{'title': 'Wikipedia', 'url': 'https://www.wikipedia.org', 'content': 'ok'}],
                        'unresponsive_engines': [],
                    })
                if host == 'searx.example.cn':
                    search_calls.append((host, params.get('engines', '')))
                    if not cn_failed_once['done']:
                        cn_failed_once['done'] = True
                        return httpx.Response(502, text='bad gateway')
                    return httpx.Response(200, json={'results': [], 'unresponsive_engines': []})
                if host == 'searx-hk.example.org':
                    search_calls.append((host, params.get('engines', '')))
                    return httpx.Response(200, json={
                        'results': [{'title': 'Python 3.14 Release', 'url': 'https://docs.python.org/3.14/', 'content': 'New features'}],
                        'unresponsive_engines': [],
                    })
                return httpx.Response(404)

            return real_client(transport=httpx.MockTransport(handler))

        cfg = SearchConfig(
            urls=['https://searx-hk.example.org', 'https://searx.example.cn'],
            region='auto',
            cache_ttl_seconds=0,
        )
        ctx = ChatToolContext(user_id=3, search_config=cfg)
        with patch.object(searx_mod.httpx, 'Client', side_effect=fake_client), chat_tool_context(ctx):
            output = self.tool.invoke({'query': 'Python 3.14'})

        # 国内网络优先选择 .cn 国内节点并传 cn_engines；该节点 502 后自动切换海外节点并自动改用 global_engines
        self.assertEqual(search_calls, [
            ('searx.example.cn', cfg.cn_engines),
            ('searx-hk.example.org', cfg.global_engines),
        ])
        self.assertIn('Python 3.14 Release', output)

    def test_disabled_or_unreachable_searxng_degrades_gracefully(self):
        import httpx
        from app.chat.tool_context import ChatToolContext, chat_tool_context
        from app.system_config import SearchConfig, SystemConfig, format_system_config
        from tools_config.search_tool import searxng as searx_mod

        # 验证 system_config.example.json 与 SystemConfig 默认格式化完全一致
        example_text = (_imports.BACKEND_ROOT / 'system_config.example.json').read_text(encoding='utf-8').replace('\r\n', '\n')
        self.assertEqual(format_system_config(SystemConfig()), example_text)

        disabled_ctx = ChatToolContext(user_id=1, search_config=SearchConfig(enabled=False))
        with chat_tool_context(disabled_ctx):
            disabled_msg = self.tool.invoke({'query': '今日新闻'})
        self.assertIn('未开启', disabled_msg)

        real_client = httpx.Client

        def broken_client(*args, **kwargs):
            def fail_handler(_request: httpx.Request):
                raise httpx.ConnectError('refused')

            return real_client(transport=httpx.MockTransport(fail_handler))

        offline_ctx = ChatToolContext(user_id=1, search_config=SearchConfig(enabled=True, cache_ttl_seconds=0))
        with patch.object(searx_mod.httpx, 'Client', side_effect=broken_client), chat_tool_context(offline_ctx):
            offline_msg = self.tool.invoke({'query': '今日新闻'})
        self.assertIn('暂不可用', offline_msg)

    def test_startup_probe_autostarts_docker_when_local_searxng_offline_and_logs_system_status(self):
        import subprocess
        import httpx
        from app.account_logging import logger
        from app.system_config import SearchConfig
        from tools_config.search_tool import searxng as searx_mod

        real_client = httpx.Client
        state = {'container_up': False}

        def fake_client(*args, **kwargs):
            def handler(request: httpx.Request):
                if 'generate_204' in str(request.url):
                    return httpx.Response(204)
                if not state['container_up']:
                    raise httpx.ConnectError('not running yet')
                return httpx.Response(200, json={'results': [], 'unresponsive_engines': []})

            return real_client(transport=httpx.MockTransport(handler))

        def fake_run(cmd, **kwargs):
            state['container_up'] = True
            return subprocess.CompletedProcess(cmd, 0, stdout='Started', stderr='')

        project_root = _imports.BACKEND_ROOT.parent
        cfg = SearchConfig(enabled=True, auto_start_docker=True, urls=['http://127.0.0.1:8080'])
        with (
            patch.object(searx_mod.httpx, 'Client', side_effect=fake_client),
            patch.object(searx_mod.shutil, 'which', side_effect=lambda name: '/usr/bin/docker' if name == 'docker' else None),
            patch.object(searx_mod.subprocess, 'run', side_effect=fake_run) as run_mock,
            self.assertLogs(logger, level='INFO') as sys_logs,
        ):
            self.router.prepare_on_startup(cfg, project_root=project_root, blocking=True, poll_interval=0.0)

        run_mock.assert_called_once()
        joined = '\n'.join(sys_logs.output)
        self.assertIn('event=searxng_autostart_triggered', joined)
        self.assertIn('event=searxng_ready', joined)
        self.assertIn('region=global', joined)
        self.assertIn('autostarted=True', joined)

    def test_outbound_rate_limiter_enforces_user_and_global_rpm_while_exempting_cache_hits(self):
        import httpx
        from app.chat.tool_context import ChatToolContext, chat_tool_context
        from app.system_config import SearchConfig
        from tools_config.search_tool import searxng as searx_mod

        real_client = httpx.Client
        outbound_calls = {'count': 0}

        def fake_client(*args, **kwargs):
            def handler(request: httpx.Request):
                if 'generate_204' in str(request.url):
                    return httpx.Response(204)
                outbound_calls['count'] += 1
                q = dict(request.url.params).get('q', '')
                return httpx.Response(200, json={
                    'results': [{'title': f'Hit {q}', 'url': f'https://example.com/{q}', 'content': 'snippet'}],
                    'unresponsive_engines': [],
                })

            return real_client(transport=httpx.MockTransport(handler))

        cfg = SearchConfig(
            urls=['http://127.0.0.1:8080'],
            region='global',
            cache_ttl_seconds=600,
            global_rpm_limit=3,
            user_rpm_limit=2,
        )
        with patch.object(searx_mod.httpx, 'Client', side_effect=fake_client):
            # 账号 1 连续发 2 个不同搜索正常放行，第 3 个不同搜索触发单账号每分钟上限
            for idx in (1, 2):
                with chat_tool_context(ChatToolContext(user_id=1, search_config=cfg)):
                    res = self.tool.invoke({'query': f'topic-{idx}'})
                    self.assertIn(f'Hit topic-{idx}', res)
            with chat_tool_context(ChatToolContext(user_id=1, search_config=cfg)):
                blocked_user = self.tool.invoke({'query': 'topic-3'})
            self.assertIn('触发限流', blocked_user)

            # 账号 1 重复查已缓存词条不受限流影响，也不额外消耗出站次数
            with chat_tool_context(ChatToolContext(user_id=1, search_config=cfg)):
                cached_res = self.tool.invoke({'query': 'topic-1'})
            self.assertIn('Hit topic-1', cached_res)
            self.assertEqual(outbound_calls['count'], 2)

            # 账号 2 第 1 次真实搜索消耗全站第 3 个名额后，全站第 4 次真实搜索被全局 RPM 拦住
            with chat_tool_context(ChatToolContext(user_id=2, search_config=cfg)):
                ok_u2 = self.tool.invoke({'query': 'topic-u2-a'})
            self.assertIn('Hit topic-u2-a', ok_u2)
            with chat_tool_context(ChatToolContext(user_id=2, search_config=cfg)):
                blocked_global = self.tool.invoke({'query': 'topic-u2-b'})
            self.assertIn('触发限流', blocked_global)
            self.assertEqual(outbound_calls['count'], 3)
