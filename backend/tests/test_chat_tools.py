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
        self.assertEqual(set(self.tools), {'get_weather', 'get_current_time'})
        for name, parameter, required in (
            ('get_weather', 'city', ['city']),
            ('get_current_time', 'timezone', []),
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
                self.assertEqual(schema['properties'][parameter]['type'], 'string')
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
