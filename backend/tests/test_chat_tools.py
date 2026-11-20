import unittest
from datetime import timezone
from unittest.mock import Mock, patch

import _imports
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

    def test_registered_time_uses_beijing_timezone_when_omitted(self):
        with patch('tools_config.time.ZoneInfo', return_value=timezone.utc) as zone:
            result = self.tools['get_current_time'].invoke({})
        zone.assert_called_once_with('Asia/Shanghai')
        self.assertRegex(result, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$')

    def test_registered_weather_executes_original_handler_and_validates_input(self):
        geocoding = Mock()
        geocoding.json.return_value = {'results': [{'name': '杭州', 'country': '中国',
                                                     'latitude': 30.2, 'longitude': 120.1}]}
        weather = Mock()
        weather.json.return_value = {'current': {'time': '2026-11-20T12:00', 'temperature_2m': 18,
                                                 'relative_humidity_2m': 60}}
        with patch('tools_config.weather_tool.requests.get', side_effect=[geocoding, weather]) as request:
            with self.assertRaises(ValidationError):
                self.tools['get_weather'].invoke({})
            with self.assertRaises(ValidationError):
                self.tools['get_weather'].invoke({'city': 42})
            request.assert_not_called()
            result = self.tools['get_weather'].invoke({'city': '杭州'})
        self.assertIn('地点：杭州', result)
        self.assertIn('气温：18°C', result)
        self.assertEqual(request.call_args_list[0].kwargs['params']['name'], '杭州')
