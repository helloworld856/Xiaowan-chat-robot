"""用隔离模型适配器测试真实路由，不连接模型服务或业务数据库。"""
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'app'))

from core.app import create_app
from test_auth import request


def load_route(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'app' / 'routers' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SettingsAPITests(unittest.TestCase):
    def setUp(self):
        from personal_model_config import PersonalModelStore
        from settings_config import SettingsStore
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.store = SettingsStore(Path(self.directory.name) / 'settings.json')
        personal_models = PersonalModelStore(Path(self.directory.name) / 'users')
        self.old_agents = (object(), object(), object())
        self.new_agents = (object(), object(), object())
        self.agents = types.ModuleType('agent_config')
        from threading import RLock
        self.agents.agent_lock = RLock()
        self.agents.analysis_agent, self.agents.text_agent, self.agents.friend_agent = self.old_agents
        self.agents.active_model = None
        self.agent_calls = []
        def create_agents(*args):
            self.agent_calls.append(args)
            return self.new_agents
        self.agents.create_agents = create_agents
        self.model_calls = []
        def create_model(*args, **kwargs):
            self.model_calls.append((args, kwargs))
            return types.SimpleNamespace(invoke=lambda _: None)
        with patch.dict(sys.modules, {
            'agent_config': self.agents,
            'agent_config.agent_factory': types.SimpleNamespace(create_model=create_model),
            'log_config': types.SimpleNamespace(logger=types.SimpleNamespace(info=lambda _: None, warning=lambda _: None)),
        }):
            self.model = load_route('tested_model_router', 'model.py')
            self.settings = load_route('tested_settings_router', 'settings.py')
        self.model.settings_store = self.store
        self.settings.settings_store = self.store
        self.environment = patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-only-placeholder'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = {'model_merchant': 'deepseek', 'model_name': 'MyModel-V2', 'base_url': 'https://example.com/v1'}
        entries = {}
        preference_store = types.SimpleNamespace(
            get=lambda user_id: entries.get(user_id, self.settings.Preferences()),
            update=lambda user_id, preferences: entries.__setitem__(user_id, preferences),
        )
        self.request_for = lambda user_id: types.SimpleNamespace(
            state=types.SimpleNamespace(user=types.SimpleNamespace(id=user_id, role='admin' if user_id == 1 else 'user')),
            app=types.SimpleNamespace(state=types.SimpleNamespace(preferences=preference_store, personal_models=personal_models)),
        )

    def current_agents(self):
        return self.agents.analysis_agent, self.agents.text_agent, self.agents.friend_agent

    def test_connection_test_does_not_save_or_switch(self):
        result = self.model.test_model(self.model.ModelConfigRequest(**self.config))
        self.assertTrue(result.status)
        self.assertIsNone(self.settings.get_settings(self.request_for(1)).model)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertEqual(self.model_calls[0][0][2], 'MyModel-V2')
        self.assertEqual(self.model_calls[0][1]['base_url'], 'https://example.com/v1')

    def test_admin_model_and_preferences_are_isolated_from_ordinary_user(self):
        self.assertTrue(self.model.validate_and_switch_model(self.model.ModelConfigRequest(**self.config)).status)
        self.assertEqual(self.current_agents(), self.new_agents)
        self.assertEqual(self.agent_calls[0][2:], ('MyModel-V2', 'https://example.com/v1'))
        self.settings.save_settings(self.settings.Preferences(send_shortcut='ctrl_enter'), self.request_for(1))
        settings_a = self.settings.get_settings(self.request_for(1))
        settings_b = self.settings.get_settings(self.request_for(2))
        self.assertTrue(settings_a.model_ready)
        self.assertFalse(settings_b.model_ready)
        self.assertIsNone(settings_b.model)
        self.assertEqual(settings_a.preferences.send_shortcut, 'ctrl_enter')
        self.assertEqual(settings_b.preferences.send_shortcut, 'enter')
        from settings_config import SettingsStore
        reloaded = SettingsStore(self.store.path).get()
        self.assertEqual(reloaded.model.model_name, 'MyModel-V2')
        self.assertEqual(reloaded.preferences.send_shortcut, 'enter')

    def test_invalid_request_does_not_write(self):
        with self.assertRaises(ValidationError):
            self.settings.Preferences(send_shortcut='unknown')
        with self.assertRaises(ValidationError):
            self.model.ModelConfigRequest(**{**self.config, 'base_url': 'file:///secret'})
        self.assertFalse(self.store.path.exists())

    def test_invalid_model_address_does_not_echo_url_parser_details(self):
        app = create_app('test')
        app.include_router(self.model.router)
        token, _ = app.state.auth.login('admin', '123456', 'test')
        address = 'https://synthetic-password＠example.test'
        status, _, response = request(app, '/model/test', 'POST', {**self.config, 'base_url': address},
                                      cookie=f'xiaowan_session={token}')
        self.assertEqual(status, 422)
        self.assertNotIn(b'synthetic-password', response)

    def test_unreadable_personal_model_does_not_block_preferences(self):
        request = self.request_for(2)
        with patch.object(request.app.state.personal_models, 'get', side_effect=OSError):
            self.assertFalse(self.settings.get_settings(request).model_ready)
            updated = self.settings.save_settings(self.settings.Preferences(send_shortcut='ctrl_enter'), request)
        self.assertEqual(updated.preferences.send_shortcut, 'ctrl_enter')
        self.assertEqual(self.settings.get_settings(request).preferences.send_shortcut, 'ctrl_enter')

    def test_model_status_reports_active_agents_after_settings_file_is_edited(self):
        request = self.model.ModelConfigRequest(**self.config)
        self.assertTrue(self.model.validate_and_switch_model(request).status)
        self.store.update_model(request.model_copy(update={'model_name': 'ManuallyEdited'}))

        settings = self.settings.get_settings(self.request_for(1))
        self.assertTrue(settings.model_ready)
        self.assertEqual(settings.model.model_name, request.model_name)

    def test_broken_settings_can_be_recovered_by_saving_a_valid_model(self):
        self.store.path.write_text('{broken', encoding='utf-8')

        settings = self.settings.get_settings(self.request_for(1))
        self.assertFalse(settings.model_ready)
        self.assertIsNone(settings.model)
        request = self.model.ModelConfigRequest(**self.config)
        self.assertTrue(self.model.validate_and_switch_model(request).status)
        self.assertEqual(self.store.get().model.model_name, request.model_name)

    def test_write_failure_keeps_active_model(self):
        with patch.object(self.store, 'update_model', side_effect=OSError('write failed')):
            result = self.model.validate_and_switch_model(self.model.ModelConfigRequest(**self.config))
        self.assertFalse(result.status)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertFalse(self.store.path.exists())

    def test_failed_connection_keeps_config_and_agents(self):
        with patch.object(self.model, 'create_model', side_effect=RuntimeError('connection unavailable')):
            result = self.model.validate_and_switch_model(self.model.ModelConfigRequest(**self.config))
        self.assertFalse(result.status)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertFalse(self.store.path.exists())

    def test_validation_timeout_does_not_switch_later(self):
        from threading import Event
        finished = Event()
        release = Event()
        def slow_call(_):
            release.wait(timeout=2)
            finished.set()
        slow_model = types.SimpleNamespace(invoke=slow_call)
        try:
            with patch.object(self.model, 'create_model', return_value=slow_model), patch.object(self.model, 'VALIDATION_TIMEOUT_SECONDS', 0.01):
                result = self.model.validate_and_switch_model(self.model.ModelConfigRequest(**self.config))
        finally:
            release.set()
            finished.wait(timeout=2)
        self.assertFalse(result.status)
        self.assertIn('超时', result.info)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertFalse(self.store.path.exists())
        # 超时调用退出后，新的连接仍能完成。
        self.assertTrue(self.model.test_model(self.model.ModelConfigRequest(**self.config)).status)

    def test_switch_waits_for_active_chat_before_saving_or_replacing_agents(self):
        from threading import Event, Thread
        ready = Event()
        done = Event()
        def prepare(*args):
            ready.set()
            return self.new_agents
        def switch():
            try:
                self.model.validate_and_switch_model(self.model.ModelConfigRequest(**self.config))
            finally:
                done.set()
        with patch.object(self.agents, 'create_agents', side_effect=prepare):
            with self.agents.agent_lock:
                worker = Thread(target=switch)
                worker.start()
                self.assertTrue(ready.wait(timeout=2))
                self.assertFalse(done.is_set())
                self.assertFalse(self.store.path.exists())
                self.assertEqual(self.current_agents(), self.old_agents)
            worker.join(timeout=2)
        self.assertTrue(done.is_set())
        self.assertEqual(self.current_agents(), self.new_agents)

    def test_model_validation_rejects_requests_when_workers_are_busy(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier, Event

        capacity = self.model.MODEL_VALIDATION_CAPACITY
        doing_work = Barrier(capacity + 1)
        release = Event()

        def slow_call(_):
            doing_work.wait(timeout=5)
            self.assertTrue(release.wait(timeout=5))

        request = self.model.ModelConfigRequest(**self.config)
        with patch.object(self.model, 'create_model', return_value=types.SimpleNamespace(invoke=slow_call)):
            with ThreadPoolExecutor(max_workers=capacity + 1) as callers:
                running = [callers.submit(self.model.test_model, request) for _ in range(capacity)]
                try:
                    doing_work.wait(timeout=5)
                    extra = callers.submit(self.model.test_model, request).result(timeout=1)
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
                results.append(self.model.validate_and_switch_model(self.model.ModelConfigRequest(**self.config)))
            finally:
                done.set()
        with self.agents.agent_lock, patch.object(self.model, 'MODEL_SWITCH_TIMEOUT_SECONDS', 0.01):
            worker = Thread(target=switch)
            worker.start()
            self.assertTrue(done.wait(timeout=2))
        worker.join(timeout=2)
        self.assertFalse(results[0].status)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertFalse(self.store.path.exists())


if __name__ == '__main__':
    unittest.main()
