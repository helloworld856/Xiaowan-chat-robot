import io
import json
import logging
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class StartupConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_logger_writes_to_console_without_erasing_previous_run(self):
        from config import configer

        log_path = self.root / 'app.log'
        log_path.write_text('previous startup\n', encoding='utf-8')
        app_logger = logging.getLogger('xiaowan')
        original_level = app_logger.level
        console = io.StringIO()
        with (patch.object(configer, 'log_save_path', log_path), patch.object(app_logger, 'handlers', []),
              redirect_stderr(console)):
            try:
                logger = runpy.run_path(str(ROOT / 'log_config' / 'log.py'))['logger']
                logger.warning('current startup')
            finally:
                for handler in app_logger.handlers:
                    handler.close()
                app_logger.setLevel(original_level)
        self.assertIn('previous startup', log_path.read_text(encoding='utf-8'))
        self.assertIn('current startup', log_path.read_text(encoding='utf-8'))
        self.assertIn('current startup', console.getvalue())

    def test_persona_requires_string_fields_and_does_not_log_its_prompt(self):
        from config import configer

        mock_logger = Mock()
        source = {'name': '小晚', 'avatar': 'xiaowan.png', 'birthday': '2004-10-13',
                  'birthplace': '北京', 'core_persona': 'private persona prompt'}
        folder = self.root / 'persona_config'
        folder.mkdir()
        path = folder / 'persona.json'
        with patch.object(configer, 'root_dir_path', str(self.root)), patch.dict(sys.modules, {
            'log_config': Mock(logger=mock_logger),
        }):
            persona = runpy.run_path(str(ROOT / 'persona_config' / 'init_persona.py'))['Persona']
            path.write_text(json.dumps(source), encoding='utf-8')
            self.assertIn(source['core_persona'], persona().FRIEND_PERSONA)
            self.assertNotIn(source['core_persona'], str(mock_logger.mock_calls))
            path.write_text(json.dumps({**source, 'name': 42}), encoding='utf-8')
            self.assertEqual(persona().BOT_NAME, '无名氏')

    def test_launcher_loads_only_the_backend_environment_and_only_when_started(self):
        from config import configer

        dotenv = SimpleNamespace(load_dotenv=Mock())
        uvicorn = SimpleNamespace(run=Mock())
        with patch.dict(sys.modules, {'dotenv': dotenv, 'uvicorn': uvicorn}), \
                patch.object(configer, 'listen_host', '127.0.0.1'), patch.object(configer, 'listen_port', 8123):
            launcher = runpy.run_path(str(ROOT / 'run_serve.py'))
            dotenv.load_dotenv.assert_not_called()
            launcher['main']()
        dotenv.load_dotenv.assert_called_once_with(ROOT / '.env')
        uvicorn.run.assert_called_once_with('app.main:app', host='127.0.0.1', port=8123, workers=1)
