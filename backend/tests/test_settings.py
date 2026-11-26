import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _imports import BACKEND_ROOT as _BACKEND_ROOT


class SettingsTests(unittest.TestCase):
    def setUp(self):
        from app.llm.admin_settings import SettingsStore
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.store = SettingsStore(self.path)

    def test_round_trip_persists_model_and_key_in_env(self):
        from pydantic import SecretStr
        from app.llm.admin_models import AdminModelStore
        from app.llm.admin_settings import ModelSettings

        store = AdminModelStore(self.path.parent / '.env')
        selected = ModelSettings(model_merchant='deepseek', model_name='MyModel-V2', base_url='https://example.com/v1/')
        store.update_model(selected, SecretStr("fake'key\\#${literal}"), replace_key=True)
        loaded = AdminModelStore(store.path).get()
        self.assertEqual(loaded.model.model_name, 'MyModel-V2')
        self.assertEqual(loaded.model.base_url, 'https://example.com/v1')
        self.assertEqual(loaded.providers['deepseek'].api_key.get_secret_value(), "fake'key\\#${literal}")
        self.assertFalse(self.path.exists())

    def test_bad_input_is_rejected(self):
        from pydantic import ValidationError
        from app.llm.admin_settings import ModelSettings
        from app.accounts.preferences import Preferences
        for address in ['file:///secret', 'https://user:password@example.com/v1', 'https://example.com/?key=secret']:
            with self.subTest(address=address), self.assertRaises(ValidationError):
                ModelSettings(model_merchant='deepseek', model_name='M', base_url=address)
        with self.assertRaises(ValidationError):
            Preferences(send_shortcut='unknown')
        with self.assertRaises(ValidationError):
            Preferences(theme='unknown')
        for theme in ('theme4', 'system'):
            self.assertEqual(Preferences(theme=theme).theme, theme)
        with self.assertRaises(ValidationError):
            Preferences(chat_font_size='unknown')

    def test_failed_write_keeps_previous_config(self):
        from pydantic import SecretStr
        from app.llm.admin_models import AdminModelStore
        from app.llm.admin_settings import ModelSettings
        store = AdminModelStore(self.path.parent / '.env')
        key = SecretStr('test-key')
        store.update_model(ModelSettings(model_merchant='deepseek', model_name='Previous'), key, replace_key=True)
        path = store.path
        # 故意让替换失败，核对旧配置字节完整且没有临时文件。
        original = path.read_bytes()
        replacement = ModelSettings(model_merchant='deepseek', model_name='Replacement')
        with patch('app.json_files.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                store.update_model(replacement, key, replace_key=True)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_legacy_settings_load_without_removed_options_and_preserve_model(self):
        self.path.write_text(json.dumps({
            'model': {'model_merchant': 'deepseek', 'model_name': 'MyModel', 'base_url': ''},
            'preferences': {'reply_length': 'short', 'split_reply': False, 'playback_mode': 'instant', 'reply_interval_ms': 2000, 'send_shortcut': 'ctrl_enter'}
        }), encoding='utf-8')
        loaded = self.store.get()
        self.assertEqual(loaded.preferences.model_dump(),
                         {'send_shortcut': 'ctrl_enter', 'theme': 'theme0', 'chat_font_size': 'standard'})
        self.assertEqual(loaded.model.model_name, 'MyModel')


if __name__ == '__main__':
    unittest.main()
