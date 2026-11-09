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


def load_route(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'app' / 'routers' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SettingsAPITests(unittest.TestCase):
    def setUp(self):
        from settings_config import SettingsStore
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.store = SettingsStore(Path(self.directory.name) / 'settings.json')
        self.old_agents = (object(), object(), object())
        self.new_agents = (object(), object(), object())
        self.agents = types.ModuleType('agent_config')
        from threading import RLock
        self.agents.agent_lock = RLock()
        self.agents.analysis_agent, self.agents.text_agent, self.agents.friend_agent = self.old_agents
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

    def current_agents(self):
        return self.agents.analysis_agent, self.agents.text_agent, self.agents.friend_agent

    def test_connection_test_does_not_save_or_switch(self):
        result = self.model.test_model(self.model.ModelConfigRequest(**self.config))
        self.assertTrue(result.status)
        self.assertIsNone(self.settings.get_settings().model)
        self.assertEqual(self.current_agents(), self.old_agents)
        self.assertEqual(self.model_calls[0][0][2], 'MyModel-V2')
        self.assertEqual(self.model_calls[0][1]['base_url'], 'https://example.com/v1')

    def test_save_model_and_preferences_survive_new_store(self):
        self.assertTrue(self.model.validate_and_switch_model(self.model.ModelConfigRequest(**self.config)).status)
        self.assertEqual(self.current_agents(), self.new_agents)
        self.assertEqual(self.agent_calls[0][2:], ('MyModel-V2', 'https://example.com/v1'))
        self.assertEqual(self.settings.save_settings(self.settings.Preferences(send_shortcut='ctrl_enter')).preferences.send_shortcut, 'ctrl_enter')
        from settings_config import SettingsStore
        reloaded = SettingsStore(self.store.path).get()
        self.assertEqual(reloaded.model.model_name, 'MyModel-V2')
        self.assertEqual(reloaded.preferences.send_shortcut, 'ctrl_enter')

    def test_invalid_request_does_not_write(self):
        with self.assertRaises(ValidationError):
            self.settings.Preferences(send_shortcut='unknown')
        with self.assertRaises(ValidationError):
            self.model.ModelConfigRequest(**{**self.config, 'base_url': 'file:///secret'})
        self.assertFalse(self.store.path.exists())

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
