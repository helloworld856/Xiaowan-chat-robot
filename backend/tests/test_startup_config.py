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


from _imports import BACKEND_ROOT as ROOT


class StartupConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_logger_only_writes_to_file_without_erasing_previous_run(self):
        from app.config import configer

        log_path = self.root / 'app.log'
        log_path.write_text('previous startup\n', encoding='utf-8')
        app_logger = logging.getLogger('xiaowan')
        original_level = app_logger.level
        console = io.StringIO()
        with (patch.object(configer, 'log_save_path', log_path), patch.object(app_logger, 'handlers', []),
              redirect_stderr(console)):
            try:
                from app.account_logging import get_logger

                logger = get_logger()
                logger.warning('current startup')
            finally:
                for handler in app_logger.handlers:
                    handler.close()
                app_logger.setLevel(original_level)
        self.assertIn('previous startup', log_path.read_text(encoding='utf-8'))
        self.assertIn('current startup', log_path.read_text(encoding='utf-8'))
        self.assertEqual(console.getvalue(), '')

    def test_launcher_shows_startup_and_keeps_runtime_logs_in_files(self):
        script = '''
import logging
import runpy
import sys
import warnings
from pathlib import Path

import uvicorn
from app.config import configer

configer.log_save_path = sys.argv[1]

def start_server(app, **options):
    uvicorn.Config(app, **options)
    from app.account_logging import logger
    from app.account_logging import account_log_context
    from app.accounts.files import UserDataPaths

    server = logging.getLogger('uvicorn.error')
    server.info('Started server process [%d]', 123)
    server.info('Waiting for application startup.')
    server.info('Application startup complete.')
    server.info('Uvicorn running on %s://%s:%d (Press CTRL+C to quit)', 'http', '127.0.0.1', 8000)
    server.error('runtime server error')
    logging.getLogger('uvicorn.access').info('%s - "%s %s HTTP/%s" %d',
                                             '127.0.0.1', 'GET', '/version', '1.1', 200)
    warnings.warn('dependency warning')
    paths = UserDataPaths(Path(sys.argv[1]).parent / 'users')
    with account_log_context(paths, 1):
        logger.info('first account event')
    with account_log_context(paths, 2):
        logger.warning('second account event')

uvicorn.run = start_server
runpy.run_path('run_serve.py', run_name='__main__')
'''
        log_path = self.root / 'app.log'
        result = subprocess.run([sys.executable, '-B', '-c', script, str(log_path)],
                                cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        contents = log_path.read_text(encoding='utf-8')
        for message in ('Started server process [123]', 'Waiting for application startup.',
                        'Application startup complete.', 'Uvicorn running on http://127.0.0.1:8000'):
            self.assertIn(message, result.stderr)
            self.assertIn(message, contents)
        for message in ('runtime server error', 'GET /version HTTP/1.1', 'dependency warning'):
            self.assertIn(message, contents)
            self.assertNotIn(message, result.stderr)
        for user_id, message in ((1, 'first account event'), (2, 'second account event')):
            account_log = self.root / 'users' / str(user_id) / 'logs' / 'app.log'
            self.assertIn(message, account_log.read_text(encoding='utf-8'))
            self.assertNotIn(message, contents)
            self.assertNotIn(message, result.stderr)

    def test_bundled_tokenizer_counts_text_without_transformers(self):
        from app.llm import token_counter

        with (patch.dict(sys.modules, {'transformers': None}),
              patch.object(token_counter, '_TOKENIZER_CACHE', None)):
            self.assertEqual(token_counter.count_tokens(''), 0)
            self.assertEqual(token_counter.count_tokens('你好，小晚'), 5)
            self.assertEqual(token_counter.count_tokens('Hello, world!'), 4)
            self.assertEqual(token_counter.count_tokens('多条回复\n123 😀'), 11)

    def test_persona_requires_string_fields_and_does_not_log_its_prompt(self):
        from app.config import configer

        mock_logger = Mock()
        source = {'name': '小晚', 'avatar': 'xiaowan.png', 'birthday': '2004-10-13',
                  'birthplace': '北京', 'core_persona': 'private persona prompt'}
        folder = self.root / 'persona_config'
        folder.mkdir()
        path = folder / 'persona.json'
        from app.llm import persona

        with (patch.object(configer, 'root_dir_path', str(self.root)),
              patch.object(persona, 'logger', mock_logger)):
            path.write_text(json.dumps(source), encoding='utf-8')
            self.assertIn(source['core_persona'], persona.Persona().FRIEND_PERSONA)
            self.assertNotIn(source['core_persona'], str(mock_logger.mock_calls))
            path.write_text(json.dumps({**source, 'name': 42}), encoding='utf-8')
            self.assertEqual(persona.Persona().BOT_NAME, '无名氏')

    def test_launcher_loads_only_the_backend_environment_and_only_when_started(self):
        from app.config import configer

        dotenv = SimpleNamespace(load_dotenv=Mock())
        uvicorn = SimpleNamespace(run=Mock())
        with patch.dict(sys.modules, {'dotenv': dotenv, 'uvicorn': uvicorn}), \
                patch('logging.basicConfig'), patch('logging.captureWarnings'), \
                patch.object(logging.getLogger('uvicorn'), 'setLevel'), \
                patch.object(logging.getLogger('uvicorn.error'), 'handlers', []), \
                patch.object(configer, 'listen_host', '127.0.0.1'), patch.object(configer, 'listen_port', 8123):
            launcher = runpy.run_path(str(ROOT / 'run_serve.py'))
            dotenv.load_dotenv.assert_not_called()
            launcher['main']()
        dotenv.load_dotenv.assert_called_once_with(ROOT / '.env')
        uvicorn.run.assert_called_once_with('app.main:app', host='127.0.0.1', port=8123, workers=1, log_config=None)
