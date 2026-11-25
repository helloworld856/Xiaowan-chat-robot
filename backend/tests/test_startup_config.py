import io
import json
import logging
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


from _imports import BACKEND_ROOT as ROOT


class StartupConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_health_url_uses_server_config_without_migrating_it(self):
        from app import system_config

        config_file = self.root / 'system_config.json'
        for host, expected_host in (('127.0.0.1', '127.0.0.1'), ('0.0.0.0', '127.0.0.1'), ('::', '[::1]')):
            original = json.dumps({'server': {'host': host, 'port': 8765}})
            config_file.write_text(original, encoding='utf-8')
            output = io.StringIO()
            with (
                self.subTest(host=host), patch.object(system_config, 'BACKEND_ROOT', self.root),
                patch.object(sys, 'argv', ['run_serve.py', '--health-url']), redirect_stdout(output),
            ):
                runpy.run_path(str(ROOT / 'run_serve.py'), run_name='__main__')
                self.assertEqual(output.getvalue().strip(), f'http://{expected_host}:8765/auth/session')
                self.assertEqual(config_file.read_text(encoding='utf-8'), original)

    def test_system_log_is_created_on_a_clean_checkout(self):
        from app.account_logging import get_logger
        from app import config

        # 模拟刚检出的项目，系统日志目录尚不存在。
        log_path = self.root / 'log_config' / 'app.log'
        app_logger = logging.getLogger('xiaowan')
        with (patch.object(config, 'SYSTEM_LOG_PATH', log_path),
              patch.object(app_logger, 'handlers', []),
              patch.object(app_logger, 'level', app_logger.level),
              redirect_stderr(io.StringIO())):
            try:
                get_logger().warning('first startup')
                self.assertTrue(log_path.is_file())
                self.assertIn('first startup', log_path.read_text(encoding='utf-8'))
            finally:
                for handler in app_logger.handlers:
                    handler.close()

    def test_get_logger_does_not_clear_an_existing_log(self):
        from app import config

        log_path = self.root / 'app.log'
        log_path.write_text('previous startup\n', encoding='utf-8')
        app_logger = logging.getLogger('xiaowan')
        original_level = app_logger.level
        console = io.StringIO()
        with (patch.object(config, 'SYSTEM_LOG_PATH', log_path), patch.object(app_logger, 'handlers', []),
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

    def test_launcher_clears_active_logs_before_writing_new_messages(self):
        script = r'''
import logging
import runpy
import sys
import warnings
from pathlib import Path

import uvicorn
from app.accounts import files
from app import config, system_config

config.SYSTEM_LOG_PATH = Path(sys.argv[1])
files.BACKEND_ROOT = Path(sys.argv[1]).parent
system_config.BACKEND_ROOT = files.BACKEND_ROOT

def start_server(app, **options):
    server_config = uvicorn.Config(app, **options)
    assert not server_config.access_log
    from app.account_logging import logger
    from app.account_logging import account_log_context
    from app.accounts.files import UserDataPaths
    from app.accounts.repository import User

    server = logging.getLogger('uvicorn.error')
    server.info('Started server process [%d]', 123)
    server.info('Waiting for application startup.')
    server.info('Application startup complete.')
    server.info('Uvicorn running on %s://%s:%d (Press CTRL+C to quit)', 'http', '127.0.0.1', 8000)
    server.error('runtime server error')
    try:
        raise RuntimeError('private ASGI failure text')
    except RuntimeError:
        server.exception('Exception in ASGI application\n')
    logging.getLogger('uvicorn.access').info('%s - "%s %s HTTP/%s" %d',
                                             '127.0.0.1', 'GET', '/version', '1.1', 200)
    warnings.warn('dependency warning')
    paths = UserDataPaths()
    with account_log_context(paths, User(1, 'First', 'user')):
        logger.info('first account event')
    with account_log_context(paths, User(2, 'Second', 'user')):
        logger.warning('second account event')

uvicorn.run = start_server
launcher = runpy.run_path('run_serve.py')
assert config.SYSTEM_LOG_PATH.read_text(encoding='utf-8') == 'previous system run'
launcher['main']()
'''
        log_path = self.root / 'app.log'
        log_path.write_text('previous system run', encoding='utf-8')
        users = self.root / 'user_data'
        for user_id in ('1', '2', '3', 'Member', 'manual'):
            directory = users / user_id / 'logs'
            directory.mkdir(parents=True)
            (directory / 'app.log').write_text('previous account run', encoding='utf-8')
        archives = (self.root / 'app.log.1', users / '1' / 'logs' / 'app.log.1')
        for archive in archives:
            archive.write_text('older archive', encoding='utf-8')
        profile = users / '1' / 'profile.json'
        profile.write_text('{"nickname":"旧昵称"}', encoding='utf-8')
        named_profile = users / 'Member' / 'profile.json'
        named_profile.write_text('{"nickname":"Member"}', encoding='utf-8')

        result = subprocess.run([sys.executable, '-B', '-c', script, str(log_path)],
                                cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        contents = log_path.read_text(encoding='utf-8')
        self.assertNotIn('previous system run', contents)
        for message in ('Started server process [123]', 'Waiting for application startup.',
                        'Application startup complete.', 'Uvicorn running on http://127.0.0.1:8000'):
            self.assertIn(message, result.stderr)
            self.assertIn(message, contents)
        for message in ('runtime server error', 'event=asgi_failure', 'dependency warning'):
            self.assertIn(message, contents)
        for message in ('runtime server error', 'event=asgi_failure', 'dependency warning'):
            self.assertIn(message, result.stderr)
        for message in ('GET /version HTTP/1.1', 'private ASGI failure text', 'Traceback'):
            self.assertNotIn(message, contents + result.stderr)
        for user_id, message in ((1, 'first account event'), (2, 'second account event')):
            account_log = (users / str(user_id) / 'logs' / 'app.log').read_text(encoding='utf-8')
            self.assertIn(message, account_log)
            self.assertNotIn('previous account run', account_log)
            self.assertNotIn(message, contents)
            self.assertNotIn(message, result.stderr)
        self.assertEqual((users / '3' / 'logs' / 'app.log').read_bytes(), b'')
        self.assertEqual((users / 'Member' / 'logs' / 'app.log').read_bytes(), b'')
        self.assertEqual((users / 'manual' / 'logs' / 'app.log').read_text(encoding='utf-8'), 'previous account run')
        for archive in archives:
            self.assertEqual(archive.read_text(encoding='utf-8'), 'older archive')
        self.assertEqual(profile.read_text(encoding='utf-8'), '{"nickname":"旧昵称"}')

    def test_bundled_tokenizer_counts_text_without_transformers(self):
        from app.llm import token_counter

        with (patch.dict(sys.modules, {'transformers': None}),
              patch.object(token_counter, '_TOKENIZER_CACHE', None)):
            self.assertEqual(token_counter.count_tokens(''), 0)
            self.assertEqual(token_counter.count_tokens('你好，小晚'), 5)
            self.assertEqual(token_counter.count_tokens('Hello, world!'), 4)
            self.assertEqual(token_counter.count_tokens('多条回复\n123 😀'), 11)

    def test_persona_requires_string_fields_and_does_not_log_its_prompt(self):
        from app import config

        mock_logger = Mock()
        source = {'name': '小晚', 'avatar': 'xiaowan.png', 'birthday': '2004-10-13',
                  'birthplace': '北京', 'core_persona': 'private persona prompt'}
        folder = self.root / 'persona_config'
        folder.mkdir()
        path = folder / 'persona.json'
        from app.llm import persona

        with (patch.object(config, 'BACKEND_ROOT', self.root),
              patch.object(persona, 'logger', mock_logger)):
            path.write_text(json.dumps(source), encoding='utf-8')
            self.assertIn(source['core_persona'], persona.Persona().FRIEND_PERSONA)
            self.assertNotIn(source['core_persona'], str(mock_logger.mock_calls))
            path.write_text(json.dumps({**source, 'name': 42}), encoding='utf-8')
            self.assertEqual(persona.Persona().BOT_NAME, '无名氏')

    def test_launcher_reads_the_configured_listener_only_when_started(self):
        from app import account_logging, config
        from app.accounts import files
        from app.system_config import ServerConfig, SystemConfig

        settings = Mock(return_value=SystemConfig(server=ServerConfig(port=8123), logging={'clear_on_startup': False}))
        uvicorn = SimpleNamespace(run=Mock())
        with patch.dict(sys.modules, {'uvicorn': uvicorn}), \
                patch('logging.basicConfig'), patch('logging.captureWarnings'), \
                patch.object(logging.getLogger('uvicorn'), 'setLevel'), \
                patch.object(logging.getLogger('uvicorn.error'), 'filters', []), \
                patch('app.account_logging.configure_system_console'), \
                patch.object(files, 'BACKEND_ROOT', self.root), \
                patch.object(config, 'SYSTEM_LOG_PATH', self.root / 'app.log'), \
                patch('app.system_config.load_system_config', settings), \
                patch('app.account_logging.reset_active_logs') as reset_logs:
            launcher = runpy.run_path(str(ROOT / 'run_serve.py'))
            self.assertIs(launcher['logger'], account_logging.logger)
            settings.assert_not_called()
            launcher['main']()
        settings.assert_called_once_with(migrate=True)
        reset_logs.assert_not_called()
        uvicorn.run.assert_called_once_with('app.main:app', host='127.0.0.1', port=8123, workers=1,
                                           log_config=None, access_log=False)
