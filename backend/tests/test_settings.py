import sys
import json
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
        self.store.update_preferences(Preferences(send_shortcut='ctrl_enter'))
        loaded = SettingsStore(self.path).get()
        self.assertEqual(loaded.model.model_name, 'MyModel-V2')
        self.assertEqual(loaded.model.base_url, 'https://example.com/v1')
        self.assertEqual(loaded.preferences.send_shortcut, 'ctrl_enter')
        self.assertEqual(loaded.preferences.model_dump(), {'send_shortcut': 'ctrl_enter'})
        self.assertNotIn('api_key', self.path.read_text(encoding='utf-8'))

    def test_bad_input_is_rejected(self):
        from pydantic import ValidationError
        from settings_config import ModelSettings, Preferences
        for address in ['file:///secret', 'https://user:password@example.com/v1', 'https://example.com/?key=secret']:
            with self.subTest(address=address), self.assertRaises(ValidationError):
                ModelSettings(model_merchant='deepseek', model_name='M', base_url=address)
        with self.assertRaises(ValidationError):
            Preferences(send_shortcut='unknown')

    def test_failed_write_keeps_previous_config(self):
        from settings_config import Preferences
        self.store.update_preferences(Preferences(send_shortcut='enter'))
        original = self.path.read_bytes()
        with patch('settings_config.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.store.update_preferences(Preferences(send_shortcut='ctrl_enter'))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_legacy_settings_load_without_removed_options_and_preserve_model(self):
        self.path.write_text(json.dumps({
            'model': {'model_merchant': 'deepseek', 'model_name': 'MyModel', 'base_url': ''},
            'preferences': {'reply_length': 'short', 'split_reply': False, 'playback_mode': 'instant', 'reply_interval_ms': 2000, 'send_shortcut': 'ctrl_enter'}
        }), encoding='utf-8')
        loaded = self.store.get()
        self.assertEqual(loaded.preferences.model_dump(), {'send_shortcut': 'ctrl_enter'})
        self.assertEqual(loaded.model.model_name, 'MyModel')
        self.store.update_preferences(loaded.preferences)
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['preferences'], {'send_shortcut': 'ctrl_enter'})
        self.assertEqual(saved['model']['model_name'], 'MyModel')


if __name__ == '__main__':
    unittest.main()
