import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        from settings_config import SettingsStore
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.store = SettingsStore(self.path)

    def test_round_trip_preserves_model_and_preferences_independently(self):
        from settings_config import ModelSettings, Preferences, SettingsStore
        self.store.update_model(ModelSettings(model_merchant='deepseek', model_name='MyModel-V2', base_url='https://example.com/v1/'))
        self.store.update_preferences(Preferences(reply_length='short', split_reply=False, playback_mode='instant', reply_interval_ms=500, send_shortcut='ctrl_enter'))
        loaded = SettingsStore(self.path).get()
        self.assertEqual(loaded.model.model_name, 'MyModel-V2')
        self.assertEqual(loaded.model.base_url, 'https://example.com/v1')
        self.assertEqual(loaded.preferences.send_shortcut, 'ctrl_enter')
        self.assertFalse(loaded.preferences.split_reply)
        self.assertNotIn('api_key', self.path.read_text(encoding='utf-8'))

    def test_bad_input_is_rejected(self):
        from pydantic import ValidationError
        from settings_config import ModelSettings, Preferences
        for address in ['file:///secret', 'https://user:password@example.com/v1', 'https://example.com/?key=secret']:
            with self.subTest(address=address), self.assertRaises(ValidationError):
                ModelSettings(model_merchant='deepseek', model_name='M', base_url=address)
        for value in [-1, 5001]:
            with self.assertRaises(ValidationError):
                Preferences(reply_interval_ms=value)
        with self.assertRaises(ValidationError):
            Preferences(reply_length='unknown')

    def test_failed_write_keeps_previous_config(self):
        from settings_config import Preferences
        self.store.update_preferences(Preferences(reply_length='short'))
        original = self.path.read_bytes()
        with patch('settings_config.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.store.update_preferences(Preferences(reply_length='detailed'))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_reply_preferences_reach_prompt_and_single_message_output(self):
        from settings_config import Preferences, reply_guidance, format_replies
        prefs = Preferences(reply_length='short', split_reply=False)
        self.assertIn('简短', reply_guidance(prefs))
        self.assertIn('一条', reply_guidance(prefs))
        self.assertEqual(format_replies(['你好', '今天如何'], prefs), ['你好\n\n今天如何'])
        self.assertEqual(format_replies(['你好', '今天如何'], Preferences()), ['你好', '今天如何'])


if __name__ == '__main__':
    unittest.main()
