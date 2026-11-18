"""用隔离模型适配器测试真实路由，不连接模型服务或业务数据库。"""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

import _imports

from app.accounts.preferences import Preferences
from app.api import model, settings
from app.application import create_app
from app.llm import configuration, factory
from app.llm.admin_models import AdminModelStore
from app.llm.admin_settings import ModelSettings
from app.llm.configuration import AdminModelRuntime, ModelConfigurationService
from app.llm.personal_models import PersonalModelStore
from test_auth import request


class SettingsAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.store = AdminModelStore(Path(self.directory.name) / '.env')
        self.model_path = self.store.path
        personal_models = PersonalModelStore(Path(self.directory.name) / 'users')
        self.old_agents = (object(), object(), object())
        self.new_agents = (object(), object(), object())
        self.runtime = AdminModelRuntime()
        self.previous = ModelSettings(model_merchant='deepseek', model_name='previous')
        self.runtime.publish(self.previous, self.old_agents)
        self.configuration = ModelConfigurationService(self.store, personal_models, self.runtime)
        self.agent_calls = self.enter_patch(factory, 'create_agents', return_value=self.new_agents)
        self.model_calls = self.enter_patch(factory, 'create_model', return_value=types.SimpleNamespace(invoke=lambda _: None))
        self.config = {'model_merchant': 'deepseek', 'model_name': 'deepseek-v4-flash', 'base_url': 'https://example.com/v1'}
        self.selection = model.ModelConfigRequest(**self.config, api_key='test-only-placeholder')
        entries = {}
        preference_store = types.SimpleNamespace(
            get=lambda user_id: entries.get(user_id, Preferences()),
            update=lambda user_id, preferences: entries.__setitem__(user_id, preferences),
        )
        self.request_for = lambda user_id: types.SimpleNamespace(
            state=types.SimpleNamespace(user=types.SimpleNamespace(id=user_id, role='admin' if user_id == 1 else 'user')),
            app=types.SimpleNamespace(state=types.SimpleNamespace(preferences=preference_store, personal_models=personal_models,
                                                      model_configuration=self.configuration)),
        )

    def enter_patch(self, module, name, **options):
        mock_patch = patch.object(module, name, **options)
        self.addCleanup(mock_patch.stop)
        return mock_patch.start()

    def current_agents(self):
        return self.runtime.snapshot()[1]

    def switch(self, selection):
        return model.validate_and_switch_model(selection, self.request_for(1))

    def probe(self, selection):
        return model.test_model(selection, self.request_for(1))

    def test_unlisted_models_are_rejected_before_network_validation(self):
        selection = model.ModelConfigRequest(model_merchant='deepseek', model_name='Unlisted', api_key='fake-key')
        self.assertFalse(self.switch(selection).status)
        from app.llm.personal_models import PersonalModel

        personal = PersonalModel(model_merchant='deepseek', model_name='Unlisted', api_key='fake-key')
        self.assertFalse(model.test_personal_model(personal, self.request_for(2)).status)
        self.assertFalse(model.save_personal_model(personal, self.request_for(2)).status)
        self.model_calls.assert_not_called()
        self.assertFalse(self.model_path.exists())

    def test_connection_test_does_not_save_or_switch(self):
        result = self.probe(self.selection)
        self.assertTrue(result.status)
        self.assertEqual(settings.get_settings(self.request_for(1)).model, self.previous)
        self.assertFalse(self.model_path.exists())
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertEqual(self.model_calls.call_args.args[2], self.config['model_name'])
        self.assertEqual(self.model_calls.call_args.kwargs['base_url'], 'https://example.com/v1')

    def test_key_presence_is_scoped_to_the_account_and_provider(self):
        from app.llm.personal_models import PersonalModel

        self.assertTrue(self.switch(self.selection).status)
        member = self.request_for(2)
        catalog = lambda request: settings.get_settings(request).model_options
        self.assertTrue(catalog(self.request_for(1)).providers['deepseek'].has_saved_key)
        self.assertFalse(catalog(member).providers['deepseek'].has_saved_key)
        store = member.app.state.personal_models
        store.update(2, PersonalModel(model_merchant='tongyi', model_name='qwen3-max', api_key='private-member-key'), ())
        self.assertTrue(catalog(member).providers['tongyi'].has_saved_key)
        self.assertFalse(catalog(self.request_for(3)).providers['tongyi'].has_saved_key)
        self.assertFalse(catalog(self.request_for(1)).providers['tongyi'].has_saved_key)
        self.assertNotIn('private-member-key', catalog(member).model_dump_json())

    def test_admin_can_save_and_reuse_a_key_without_returning_it_in_settings(self):
        selection = model.ModelConfigRequest(**self.config, api_key='new-admin-key')
        self.assertTrue(self.probe(selection).status)
        self.assertFalse(self.model_path.exists())
        self.assertTrue(self.switch(selection).status)
        self.assertTrue(self.probe(model.ModelConfigRequest(**self.config)).status)
        self.assertEqual(self.model_calls.call_args.args[1], 'new-admin-key')
        self.assertNotIn('api_key', settings.get_settings(self.request_for(1)).model_dump_json())
        self.assertNotIn('new-admin-key', settings.get_settings(self.request_for(1)).model_dump_json())

    def test_switching_to_another_provider_preserves_the_first_provider_key(self):
        self.assertTrue(self.switch(self.selection).status)
        tongyi = model.ModelConfigRequest(model_merchant='tongyi', model_name='qwen3-max', api_key='second-key')
        self.assertTrue(self.switch(tongyi).status)
        self.assertTrue(self.switch(model.ModelConfigRequest(**self.config)).status)
        self.assertEqual(self.agent_calls.call_args.args[1], 'test-only-placeholder')
        self.assertEqual(self.store.get().providers['tongyi'].api_key.get_secret_value(), 'second-key')

    def test_admin_reuses_the_provider_address_and_key_when_switching_models(self):
        self.assertTrue(self.switch(self.selection).status)
        second = model.ModelConfigRequest(model_merchant='deepseek', model_name='deepseek-v4-pro')
        self.assertTrue(self.switch(second).status)
        self.assertEqual(self.agent_calls.call_args.args[1], 'test-only-placeholder')
        self.assertEqual(self.agent_calls.call_args.kwargs['base_url'], self.config['base_url'])
        self.assertEqual(self.store.get().providers['deepseek'].model_name, 'deepseek-v4-pro')

    def test_admin_model_and_preferences_are_isolated_from_ordinary_user(self):
        self.assertTrue(self.switch(self.selection).status)
        self.assertEqual(self.current_agents(), self.new_agents)
        self.assertEqual(self.agent_calls.call_args.args[2], self.config['model_name'])
        self.assertEqual(self.agent_calls.call_args.kwargs['base_url'], 'https://example.com/v1')
        settings.save_settings(Preferences(send_shortcut='ctrl_enter'), self.request_for(1))
        settings_a = settings.get_settings(self.request_for(1))
        settings_b = settings.get_settings(self.request_for(2))
        self.assertTrue(settings_a.model_ready)
        self.assertFalse(settings_b.model_ready)
        self.assertIsNone(settings_b.model)
        self.assertEqual(settings_a.preferences.send_shortcut, 'ctrl_enter')
        self.assertEqual(settings_b.preferences.send_shortcut, 'enter')
        self.assertEqual(settings_a.model_options.providers['deepseek'].saved_address,
                         'https://example.com/v1')
        self.assertIsNone(settings_b.model_options.providers['deepseek'].saved_address)
        self.assertEqual(settings_b.model_options.providers['deepseek'].names, ['deepseek-v4-flash', 'deepseek-v4-pro'])
        reloaded = AdminModelStore(self.store.path).get()
        self.assertEqual(reloaded.model.model_name, self.config['model_name'])
        personal_path = self.request_for(2).app.state.personal_models.path_for(2)
        self.assertNotIn('test-only-placeholder', personal_path.read_text(encoding='utf-8'))

    def test_invalid_request_does_not_write(self):
        with self.assertRaises(ValidationError):
            Preferences(send_shortcut='unknown')
        with self.assertRaises(ValidationError):
            model.ModelConfigRequest(**{**self.config, 'base_url': 'file:///secret'})
        self.assertFalse(self.model_path.exists())

    def test_invalid_model_address_does_not_echo_url_parser_details(self):
        app = create_app('test')
        app.state.model_configuration = self.configuration
        app.include_router(model.router)
        token, _ = app.state.auth.login('admin', '123456', 'test')
        address = 'https://synthetic-password＠example.test'
        status, _, response = request(app, '/model/test', 'POST', {**self.config, 'base_url': address},
                                      cookie=f'xiaowan_session={token}')
        self.assertEqual(status, 422)
        self.assertNotIn(b'synthetic-password', response)

    def test_unreadable_personal_model_does_not_block_preferences(self):
        request = self.request_for(2)
        with patch.object(request.app.state.personal_models, 'get', side_effect=OSError):
            self.assertFalse(settings.get_settings(request).model_ready)
            updated = settings.save_settings(Preferences(send_shortcut='ctrl_enter'), request)
        self.assertEqual(updated.preferences.send_shortcut, 'ctrl_enter')
        self.assertEqual(settings.get_settings(request).preferences.send_shortcut, 'ctrl_enter')

    def test_model_status_reports_active_agents_after_settings_file_is_edited(self):
        request = self.selection
        self.assertTrue(self.switch(request).status)
        self.store.update_model(request.selection().model_copy(update={'model_name': 'ManuallyEdited'}),
                                request.api_key, replace_key=True)

        current = settings.get_settings(self.request_for(1))
        self.assertTrue(current.model_ready)
        self.assertEqual(current.model.model_name, request.model_name)

    def test_broken_settings_can_be_recovered_by_saving_a_valid_model(self):
        self.configuration.admin_runtime = AdminModelRuntime()
        self.model_path.write_text('DEFAULT_MODEL_MERCHANT=invalid\nDEFAULT_MODEL_NAME=broken\n', encoding='utf-8')

        current = settings.get_settings(self.request_for(1))
        self.assertFalse(current.model_ready)
        self.assertIsNone(current.model)
        request = self.selection
        self.assertTrue(self.switch(request).status)
        self.assertEqual(self.store.get().model.model_name, request.model_name)

    def test_write_failure_keeps_active_model(self):
        with patch.object(self.store, 'update_model', side_effect=OSError('write failed')):
            result = self.switch(self.selection)
        self.assertFalse(result.status)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertFalse(self.model_path.exists())

    def test_failed_connection_keeps_config_and_agents(self):
        with patch.object(factory, 'create_model', side_effect=RuntimeError('connection unavailable')):
            result = self.switch(self.selection)
        self.assertFalse(result.status)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertFalse(self.model_path.exists())

    def test_validation_timeout_does_not_switch_later(self):
        from threading import Event
        finished = Event()
        release = Event()
        def slow_call(_):
            release.wait(timeout=2)
            finished.set()
        slow_model = types.SimpleNamespace(invoke=slow_call)
        try:
            with patch.object(factory, 'create_model', return_value=slow_model), patch.object(configuration, 'VALIDATION_TIMEOUT_SECONDS', 0.01):
                result = self.switch(self.selection)
        finally:
            release.set()
            finished.wait(timeout=2)
        self.assertFalse(result.status)
        self.assertIn('超时', result.info)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertFalse(self.model_path.exists())
        # 超时调用退出后，新的连接仍能完成。
        self.assertTrue(self.probe(self.selection).status)

    def test_switch_waits_for_active_chat_before_saving_or_replacing_agents(self):
        from threading import Event, Thread
        ready = Event()
        done = Event()
        def prepare(*args, **kwargs):
            ready.set()
            return self.new_agents
        def switch():
            try:
                self.switch(self.selection)
            finally:
                done.set()
        with patch.object(factory, 'create_agents', side_effect=prepare):
            with self.runtime.lock:
                worker = Thread(target=switch)
                worker.start()
                self.assertTrue(ready.wait(timeout=2))
                self.assertFalse(done.is_set())
                self.assertFalse(self.model_path.exists())
                self.assertEqual(self.current_agents(), self.old_agents)
            worker.join(timeout=2)
        self.assertTrue(done.is_set())
        self.assertEqual(self.current_agents(), self.new_agents)

    def test_model_validation_rejects_requests_when_workers_are_busy(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier, Event

        capacity = configuration.MODEL_VALIDATION_CAPACITY
        # 让已有探测都占住名额，再发一个请求验证立即拒绝。
        doing_work = Barrier(capacity + 1)
        release = Event()

        def slow_call(_):
            doing_work.wait(timeout=5)
            self.assertTrue(release.wait(timeout=5))

        request = self.selection
        with patch.object(factory, 'create_model', return_value=types.SimpleNamespace(invoke=slow_call)):
            with ThreadPoolExecutor(max_workers=capacity + 1) as callers:
                running = [callers.submit(self.probe, request) for _ in range(capacity)]
                try:
                    doing_work.wait(timeout=5)
                    extra = callers.submit(self.probe, request).result(timeout=1)
                    self.assertFalse(extra.status)
                    self.assertIn('繁忙', extra.info)
                finally:
                    release.set()
                self.assertTrue(all(item.result(timeout=5).status for item in running))

    def test_busy_chat_timeout_does_not_activate_model_later(self):
        from threading import Event, Thread
        done = Event()
        results = []
        def switch():
            try:
                results.append(self.switch(self.selection))
            finally:
                done.set()
        with self.runtime.lock, patch.object(configuration, 'MODEL_SWITCH_TIMEOUT_SECONDS', 0.01):
            worker = Thread(target=switch)
            worker.start()
            self.assertTrue(done.wait(timeout=2))
        worker.join(timeout=2)
        self.assertFalse(results[0].status)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertFalse(self.model_path.exists())


if __name__ == '__main__':
    unittest.main()
