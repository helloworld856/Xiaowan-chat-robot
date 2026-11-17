import io
import json
import logging
import runpy
import subprocess
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

    def test_logger_only_writes_to_file_without_erasing_previous_run(self):
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
        self.assertEqual(console.getvalue(), '')

    def test_launcher_writes_server_logs_and_warnings_to_file(self):
        script = '''
import logging
import runpy
import sys
import warnings

import uvicorn
from config import configer

configer.log_save_path = sys.argv[1]

def start_server(app, **options):
    uvicorn.Config(app, **options)
    logging.getLogger('uvicorn.error').info('server startup')
    logging.getLogger('uvicorn.access').info('%s - "%s %s HTTP/%s" %d',
                                             '127.0.0.1', 'GET', '/version', '1.1', 200)
    warnings.warn('dependency warning')

uvicorn.run = start_server
runpy.run_path('run_serve.py', run_name='__main__')
'''
        log_path = self.root / 'app.log'
        result = subprocess.run([sys.executable, '-B', '-c', script, str(log_path)],
                                cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')
        contents = log_path.read_text(encoding='utf-8')
        self.assertIn('server startup', contents)
        self.assertIn('GET /version HTTP/1.1', contents)
        self.assertIn('dependency warning', contents)

    def test_bundled_tokenizer_counts_text_without_transformers(self):
        from utils import tokens_count

        with (patch.dict(sys.modules, {'transformers': None}),
              patch.object(tokens_count, '_TOKENIZER_CACHE', None)):
            self.assertEqual(tokens_count.count_tokens(''), 0)
            self.assertEqual(tokens_count.count_tokens('你好，小晚'), 5)
            self.assertEqual(tokens_count.count_tokens('Hello, world!'), 4)
            self.assertEqual(tokens_count.count_tokens('多条回复\n123 😀'), 11)

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
                patch('logging.basicConfig'), patch('logging.captureWarnings'), \
                patch.object(logging.getLogger('uvicorn'), 'setLevel'), \
                patch.object(configer, 'listen_host', '127.0.0.1'), patch.object(configer, 'listen_port', 8123):
            launcher = runpy.run_path(str(ROOT / 'run_serve.py'))
            dotenv.load_dotenv.assert_not_called()
            launcher['main']()
        dotenv.load_dotenv.assert_called_once_with(ROOT / '.env')
        uvicorn.run.assert_called_once_with('app.main:app', host='127.0.0.1', port=8123, workers=1, log_config=None)
