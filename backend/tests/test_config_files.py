import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import _imports


class ConfigFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_older_global_config_is_extended_with_comments_and_keeps_existing_values(self):
        from app.system_config import load_system_config

        path = self.root / 'system_config.json'
        path.write_text('{"database":{"password":"p//w"},"chat":{"queue_size":19}}', encoding='utf-8')
        loaded = load_system_config(self.root, migrate=True)
        self.assertEqual(loaded.database.password.get_secret_value(), 'p//w')
        self.assertEqual(loaded.chat.queue_size, 19)
        self.assertEqual(loaded.auth.session_ttl_seconds, 28800)
        self.assertIn('//', path.read_text(encoding='utf-8'))
        self.assertIn('"session_ttl_seconds": 28800', path.read_text(encoding='utf-8'))
        self.assertEqual(load_system_config(self.root), loaded)

    def test_system_catalog_migrates_to_lists_and_backs_up_old_address_overrides(self):
        from app.json_files import strip_json_line_comments
        from app.system_config import load_system_config

        path = self.root / 'system_config.json'
        text = (_imports.BACKEND_ROOT / 'system_config.example.json').read_text(encoding='utf-8')
        data = json.loads(strip_json_line_comments(text))
        data['models']['providers']['deepseek']['models'] = {
            'deepseek-v4-flash': {'base_url': 'https://old-model.example/v1'}, 'deepseek-v4-pro': {},
        }
        path.write_text(json.dumps(data), encoding='utf-8')
        catalog = load_system_config(self.root, migrate=True).models
        self.assertEqual(catalog.providers['deepseek'].default_model, 'deepseek-v4-flash')
        self.assertEqual(catalog.endpoint('deepseek').base_url, 'https://api.deepseek.com')
        self.assertEqual(catalog.providers['deepseek'].models, ['deepseek-v4-flash', 'deepseek-v4-pro'])
        self.assertTrue(path.with_name('system_config.json.before-provider.bak').exists())

    def test_commented_config_preserves_operator_notes_on_restarts(self):
        from app.system_config import load_system_config

        path = self.root / 'system_config.json'
        text = '{\n // 用户自己记的说明\n "chat": {"clear_history_on_startup": false}\n}\n'
        path.write_text(text, encoding='utf-8')
        self.assertFalse(load_system_config(self.root, migrate=True).chat.clear_history_on_startup)
        self.assertEqual(path.read_text(encoding='utf-8'), text)

    def test_personal_model_switch_reuses_the_provider_address_and_key(self):
        from app.llm.personal_models import PersonalModel, PersonalModelStore

        store = PersonalModelStore(self.root / 'user_data')
        first = PersonalModel(model_merchant='deepseek', model_name='first', api_key='first-key')
        second = PersonalModel(model_merchant='deepseek', model_name='second')
        store.update(12, first, (object(),) * 3)
        store.update(12, second, (object(),) * 3)
        path = store.path_for(12)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['providers']['deepseek']['base_url'] = 'https://saved-provider.example/v1'
        path.write_text(json.dumps(data), encoding='utf-8')
        with patch('app.llm.factory.create_agents') as create_agents:
            store.agents_for(12)
        self.assertEqual(create_agents.call_args.kwargs['base_url'], 'https://saved-provider.example/v1')
        self.assertEqual(data['providers']['deepseek']['model_name'], 'second')
        self.assertEqual(data['providers']['deepseek']['api_key'], 'first-key')
        self.assertEqual(set(data['providers']['deepseek']), {'base_url', 'model_name', 'api_key'})

    def test_grouped_personal_config_keeps_the_active_record_and_backs_up_other_profiles(self):
        from app.llm.personal_models import PersonalModelStore

        store = PersonalModelStore(self.root / 'users')
        path = store.path_for(12)
        path.parent.mkdir(parents=True)
        old = {
            'active': {'model_merchant': 'deepseek', 'model_name': 'second'},
            'models': {'deepseek': {
                'first': {'base_url': 'https://first.example/v1', 'adapter': 'openai', 'api_key': 'old-first'},
                'second': {'base_url': 'https://second.example/v1', 'adapter': 'openai', 'api_key': 'old-second'},
            }},
        }
        path.write_text(json.dumps(old), encoding='utf-8')
        active = store.get(12)
        self.assertEqual(active.base_url, 'https://second.example/v1')
        self.assertEqual(active.api_key.get_secret_value(), 'old-second')
        backup = path.with_name(path.name + '.before-provider.bak')
        self.assertEqual(json.loads(backup.read_text(encoding='utf-8')), old)

    def test_system_config_migrates_legacy_values_once_and_uses_json_afterward(self):
        from app.json_files import strip_json_line_comments
        from app.system_config import load_system_config

        legacy = self.root / '.env'
        legacy.write_text('PASSWORD=old-password\nXIAOWAN_CHAT_QUEUE_SIZE=15\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True):
            path = self.root / 'system_config.json'
            self.assertEqual(load_system_config(self.root).chat.queue_size, 15)
            self.assertFalse(path.exists())
            config = load_system_config(self.root, migrate=True)
            data = json.loads(strip_json_line_comments(path.read_text(encoding='utf-8')))
            self.assertEqual(config.database.password.get_secret_value(), 'old-password')
            self.assertEqual(data['database']['password'], 'old-password')
            self.assertEqual(data['chat']['queue_size'], 15)
            path.write_text(json.dumps({**data, 'server': {'host': '127.0.0.1', 'port': 8123}}), encoding='utf-8')
            legacy.write_text('PASSWORD=other-password\nXIAOWAN_CHAT_QUEUE_SIZE=1\n', encoding='utf-8')
            reloaded = load_system_config(self.root, migrate=True)
        self.assertEqual(reloaded.database.password.get_secret_value(), 'old-password')
        self.assertEqual(reloaded.chat.queue_size, 15)
        self.assertEqual(reloaded.server.port, 8123)

    def test_invalid_system_config_reports_filename_without_credentials(self):
        from app.system_config import load_system_config

        path = self.root / 'system_config.json'
        path.write_text('{"database":{"password":"private-test-value"},"chat":{"queue_size":0}}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, r'system_config\.json') as error:
            load_system_config(self.root, migrate=True)
        self.assertNotIn('private-test-value', str(error.exception))

    def test_admin_import_preserves_other_env_entries_and_runs_once(self):
        from dotenv import dotenv_values
        from app.llm.admin_models import AdminModelStore, import_legacy_admin_model
        from app.llm.admin_settings import ModelSettings

        path = self.root / '.env'
        path.write_text('# keep this\nPASSWORD=old-db-password\nDEFAULT_MODEL_MERCHANT=tongyi\n'
                        'DEFAULT_MODEL_NAME=ignored-old-placeholder\n', encoding='utf-8')
        store = AdminModelStore(path)
        selection = ModelSettings(model_merchant='deepseek', model_name='legacy-model', base_url='https://example.test/v1')
        imported = store.initialize(lambda: import_legacy_admin_model(selection, {
            'DEEPSEEK_API_KEY': 'test-deepseek', 'TONGYI_API_KEY': 'test-tongyi',
        }))
        self.assertEqual(imported.model, selection)
        self.assertEqual(imported.key_for('tongyi').get_secret_value(), 'test-tongyi')
        values = dotenv_values(path)
        self.assertEqual(values['PASSWORD'], 'old-db-password')
        providers = json.loads(values['ADMIN_PROVIDERS'])
        self.assertEqual(providers['deepseek']['base_url'],
                         'https://example.test/v1')
        self.assertEqual(providers['deepseek']['model_name'], 'legacy-model')
        self.assertNotIn('DEEPSEEK_API_KEY', values)
        self.assertIn('# keep this', path.read_text(encoding='utf-8'))
        self.assertEqual(store.initialize(lambda: self.fail('Legacy config read again')), imported)

    def test_admin_json_migration_prefers_its_model_and_keeps_other_provider_keys(self):
        from app.llm.admin_models import AdminModelStore, import_legacy_admin_model
        from app.llm.admin_settings import ModelSettings

        previous = self.root / 'model.json'
        model = {'model_merchant': 'tongyi', 'model_name': 'saved-model', 'base_url': 'https://example.test/v1'}
        previous.write_text(json.dumps({'model': model, 'api_keys': {'tongyi': 'json-key'}}), encoding='utf-8')
        store = AdminModelStore(self.root / '.env')
        legacy = ModelSettings(model_merchant='deepseek', model_name='older-model')
        store.initialize(lambda: import_legacy_admin_model(legacy, {
            'DEEPSEEK_API_KEY': 'kept-key', 'TONGYI_API_KEY': 'older-key',
        }, previous=previous))
        state = store.get()
        self.assertEqual(state.model.model_dump(), model)
        self.assertEqual(state.key_for('tongyi').get_secret_value(), 'json-key')
        self.assertEqual(state.key_for('deepseek').get_secret_value(), 'kept-key')

    def test_grouped_admin_config_backs_up_old_entries_and_keeps_the_active_model(self):
        from dotenv import dotenv_values
        from app.llm.admin_models import AdminModelStore

        path = self.root / '.env'
        old = {'deepseek': {
            'first': {'base_url': 'https://first.example/v1', 'api_key': 'old-first'},
            'second': {'base_url': 'https://second.example/v1', 'api_key': 'old-second'},
        }}
        path.write_text('DEFAULT_MODEL_MERCHANT=deepseek\nDEFAULT_MODEL_NAME=second\n'
                        f"ADMIN_MODELS='{json.dumps(old)}'\n", encoding='utf-8')
        state = AdminModelStore(path).initialize(lambda: self.fail('Grouped config should be migrated directly'))
        self.assertEqual(state.model.model_name, 'second')
        self.assertEqual(state.model.base_url, 'https://second.example/v1')
        self.assertEqual(state.key_for('deepseek').get_secret_value(), 'old-second')
        backup = dotenv_values(self.root / '.env.before-provider.bak', interpolate=False)
        self.assertEqual(json.loads(backup['ADMIN_MODELS']), old)

    def test_personal_model_file_has_an_official_address_before_model_setup(self):
        from app.llm.personal_models import PersonalModel, PersonalModelStore

        store = PersonalModelStore(self.root / 'user_data')
        store.ensure(12)
        path = store.path_for(12)
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertIsNone(data['active_provider'])
        self.assertEqual(data['providers']['deepseek']['base_url'], 'https://api.deepseek.com')
        self.assertEqual(data['providers']['tongyi']['base_url'], 'https://dashscope.aliyuncs.com/api/v1')
        selection = PersonalModel(model_merchant='tongyi', model_name='my-model', api_key='fake-key')
        store.update(12, selection, ())
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['providers']['tongyi']['base_url'],
                         'https://dashscope.aliyuncs.com/api/v1')

    def test_existing_personal_model_gets_its_official_address_without_changing_the_key(self):
        from app.llm.personal_models import PersonalModelStore

        store = PersonalModelStore(self.root / 'user_data')
        path = store.path_for(12)
        path.parent.mkdir(parents=True)
        original = {'model_merchant': 'tongyi', 'model_name': 'legacy-model', 'api_key': 'legacy-test-key'}
        path.write_text(json.dumps({'model': original}), encoding='utf-8')
        self.assertEqual(store.get(12).public_model().base_url, 'https://dashscope.aliyuncs.com/api/v1')
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['active_provider'], 'tongyi')
        self.assertEqual(saved['providers']['tongyi']['model_name'], 'legacy-model')
        self.assertEqual(saved['providers']['tongyi']['api_key'], 'legacy-test-key')

    def test_database_pool_uses_system_config(self):
        from pydantic import SecretStr
        from app.database.mysql import open_pool
        from app.system_config import DatabaseConfig

        database = DatabaseConfig(host='db.internal', port=3307, user='app', password=SecretStr('test-password'),
                                  name='AppDatabase')
        with patch('app.database.mysql.pymysql.connect') as connect:
            pool = open_pool(config=database)
            try:
                with pool.connection():
                    args = connect.call_args.kwargs
                    self.assertEqual((args['host'], args['port'], args['user'], args['password'], args['database']),
                                     ('db.internal', 3307, 'app', 'test-password', 'AppDatabase'))
            finally:
                pool.close()

    def test_application_startup_migrates_the_actual_admin_and_uses_chat_limits(self):
        from fastapi.testclient import TestClient
        from app.accounts.profile import ProfileStore
        from app.accounts.repository import User
        from app.application import build_application
        from app.llm.admin_settings import SettingsStore
        from app.system_config import ChatConfig

        legacy = self.root / 'auth_config'
        legacy.mkdir()
        (self.root / '.env').write_text('DEEPSEEK_API_KEY=old-admin-key\n', encoding='utf-8')
        old_settings = self.root / 'settings.json'
        old_settings.write_text('{"model":{"model_merchant":"deepseek","model_name":"OldModel"}}', encoding='utf-8')
        db = MagicMock()
        with (patch.dict('os.environ', {}, clear=True),
              patch('app.application.settings_store', SettingsStore(old_settings)),
              patch('app.application.SQLAccountStore') as accounts,
              patch('app.application.initialize_schema'), patch('app.application.reset_chat_history') as reset,
              patch('app.accounts.preferences.UserPreferencesStore.find', return_value=None),
              patch('app.llm.factory.create_agents', return_value=(object(), object(), object())) as agents):
            accounts.return_value.seed_admin.return_value = User(12, 'admin', 'admin')
            app = build_application(db, ProfileStore(self.root / 'user_data'), legacy,
                                    chat_config=ChatConfig(concurrency=6, queue_size=10, queue_timeout=3,
                                                           clear_history_on_startup=False))
            with TestClient(app):
                file = app.state.model_configuration.admin_models.get()
                self.assertEqual(file.model.model_name, 'OldModel')
                self.assertEqual(file.key_for('deepseek').get_secret_value(), 'old-admin-key')
                self.assertFalse((self.root / 'user_data' / '12' / 'model.json').exists())
                self.assertEqual(agents.call_args.args, ('deepseek', 'old-admin-key', 'OldModel'))
                self.assertEqual(agents.call_args.kwargs['base_url'], 'https://api.deepseek.com')
                self.assertEqual(app.state.chat._max_waiting, 10)
                self.assertEqual(app.state.chat._wait_timeout, 3)
                reset.assert_not_called()
