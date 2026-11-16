"""Optional MySQL 8 integration test. Only a randomly named test database is changed."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@unittest.skipUnless(os.getenv('XIAOWAN_TEST_MYSQL_PORT'), 'requires an isolated MySQL instance')
class MultiUserMySQLTest(unittest.IsolatedAsyncioTestCase):
    async def test_migration_account_isolation_concurrent_graphs_and_restart(self):
        import httpx
        import pymysql

        import agent_config
        from app import main
        from auth_config import hash_password
        from database_config import open_pool
        from profile_config import ProfileStore
        from routers import settings as settings_router
        from settings_config import SettingsStore

        database = f'xiaowan_test_{uuid4().hex}'
        db_options = {'host': '127.0.0.1', 'port': int(os.environ['XIAOWAN_TEST_MYSQL_PORT']),
                      'user': 'root', 'password': os.getenv('XIAOWAN_TEST_MYSQL_PASSWORD', '')}

        def drop_test_database():
            with pymysql.connect(**db_options) as connection, connection.cursor() as cursor:
                cursor.execute(f'DROP DATABASE IF EXISTS `{database}`')

        self.addCleanup(drop_test_database)
        pool = open_pool(database, **db_options)
        self.addCleanup(pool.close)
        with pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute('CREATE TABLE history_messages (conversation_round INT PRIMARY KEY, user MEDIUMTEXT, assistant MEDIUMTEXT) ENGINE=MyISAM')
            cursor.execute('CREATE TABLE abstract_messages (abstract MEDIUMTEXT) ENGINE=MyISAM')
            cursor.execute("INSERT INTO history_messages VALUES (0, 'legacy history', 'legacy reply')")
            cursor.execute("INSERT INTO abstract_messages VALUES ('legacy summary')")
            connection.commit()

        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            folder = Path(directory)
            credential = 'test admin password'
            (folder / 'admin.json').write_text(json.dumps({'username': 'admin', 'password_hash': hash_password(credential)}), encoding='utf-8')
            (folder / 'profile.json').write_text('{"nickname":"旧昵称","avatar_id":"frog"}', encoding='utf-8')
            profiles = ProfileStore(folder / 'profiles')
            model_settings = SettingsStore(folder / 'settings.json')
            model_settings.path.write_text(json.dumps({
                'model': {'model_merchant': 'deepseek', 'model_name': 'test-model'},
                'preferences': {'send_shortcut': 'ctrl_enter'},
            }), encoding='utf-8')
            started = Event()
            release = Event()

            def response(content):
                return {'messages': [SimpleNamespace(content=content, response_metadata={})]}

            def analyze(payload):
                prompt = payload['messages'][0]['content']
                if 'wait for B' in prompt:
                    started.set()
                    if not release.wait(10):
                        raise TimeoutError('B did not finish independently')
                return response('{"intent":"test","emotion":"平静","answer":"日常互动"}')

            def reply(payload, label):
                prompt = payload['messages'][0]['content']
                if 'message B' in prompt:
                    self.assertNotIn('wait for B', prompt)
                return response(json.dumps({'reply': [f'{label}|||标记']}, ensure_ascii=False))

            admin_key = 'admin-only-placeholder'
            personal_key = 'personal-placeholder'

            def create_agents(merchant, key, name, base_url):
                self.assertEqual(base_url, '')
                self.assertIn(key, (admin_key, personal_key))
                return (SimpleNamespace(invoke=analyze), SimpleNamespace(invoke=lambda _: response('summary')),
                        SimpleNamespace(invoke=lambda payload: reply(payload, name)))

            with patch.object(main, 'settings_store', model_settings), patch.object(settings_router, 'settings_store', model_settings), \
                    patch.dict(os.environ, {'DEEPSEEK_API_KEY': admin_key}), \
                    patch.object(agent_config, 'analysis_agent', None), patch.object(agent_config, 'text_agent', None), \
                    patch.object(agent_config, 'friend_agent', None), patch.object(agent_config, 'active_model', None), \
                    patch.object(agent_config, 'create_agents', side_effect=create_agents) as factory, \
                    patch.object(main.model, 'create_model', return_value=SimpleNamespace(invoke=lambda _: None)) as validator:
                app = main.build_application(pool, profiles, folder)
                async with app.router.lifespan_context(app):
                    with pool.connection() as connection, connection.cursor() as cursor:
                        cursor.execute('''
                            SELECT table_name, engine FROM information_schema.tables
                            WHERE table_schema = DATABASE() AND table_name IN ('history_messages', 'abstract_messages')
                        ''')
                        self.assertEqual(dict(cursor.fetchall()), {
                            'history_messages': 'InnoDB', 'abstract_messages': 'InnoDB',
                        })
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as a, \
                            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as b:
                        admin_info = (await a.post('/auth/login', json={'username': 'admin', 'password': credential})).json()['user']
                        self.assertEqual(admin_info['role'], 'admin')
                        self.assertEqual((await a.get('/profile')).json()['nickname'], '旧昵称')
                        self.assertEqual((await a.get('/settings')).json()['preferences']['send_shortcut'], 'ctrl_enter')
                        user_credential = 'UserTest!2026'
                        registration = {'username': 'Second_User', 'password': user_credential, 'confirm_password': user_credential}
                        first, second = await asyncio.gather(b.post('/auth/register', json=registration),
                            b.post('/auth/register', json={**registration, 'username': 'second_user'}))
                        self.assertEqual(sorted([first.status_code, second.status_code]), [201, 409])
                        login_b = await b.post('/auth/login', json={'username': 'second_user', 'password': user_credential})
                        self.assertEqual(login_b.status_code, 200, login_b.text)
                        self.assertEqual(login_b.json()['user']['role'], 'user')
                        personal_file = profiles.root / str(login_b.json()['user']['id']) / 'model.json'
                        self.assertEqual(json.loads(personal_file.read_text(encoding='utf-8')), {'model': None})
                        self.assertFalse((profiles.root / str(admin_info['id']) / 'model.json').exists())
                        self.assertFalse((await b.get('/settings')).json()['model_ready'])
                        self.assertEqual(factory.call_args.args[1], admin_key)
                        rejected = await b.post('/chat', json={'user_input': 'must not use admin'})
                        self.assertEqual(rejected.status_code, 403, rejected.text)
                        self.assertEqual(rejected.json()['detail']['code'], 'CHAT_NOT_STARTED')
                        self.assertEqual(factory.call_count, 1)
                        options = {'model_merchant': 'deepseek', 'model_name': 'blocked'}
                        for path in ('/model', '/model/test'):
                            denied = await b.post(path, json=options)
                            self.assertEqual(denied.status_code, 403, denied.text)
                        personal = {**options, 'model_name': 'my-model', 'api_key': personal_key}
                        self.assertEqual((await a.post('/model/personal', json=personal)).status_code, 403)
                        invalid = await b.post('/model/personal', json={**personal, 'base_url': 'https://example.com'})
                        self.assertEqual(invalid.status_code, 422)
                        self.assertNotIn(personal_key, invalid.text)
                        test = await b.post('/model/personal/test', json=personal)
                        self.assertTrue(test.json()['status'], test.text)
                        self.assertEqual(json.loads(personal_file.read_text(encoding='utf-8')), {'model': None})
                        self.assertTrue((await b.post('/model/personal', json=personal)).json()['status'])
                        self.assertEqual(validator.call_args.args[1], personal_key)
                        self.assertEqual(factory.call_args.args[1], personal_key)
                        public_settings = (await b.get('/settings')).json()
                        self.assertTrue(public_settings['model_ready'])
                        self.assertNotIn('api_key', public_settings['model'])
                        self.assertEqual(public_settings['model']['model_name'], 'my-model')
                        self.assertEqual((await a.get('/settings')).json()['model']['model_name'], 'test-model')
                        updated_b = await b.post('/profile', json={'nickname': '第二个账号', 'avatar_id': 'fox'})
                        self.assertEqual(updated_b.status_code, 200, updated_b.text)
                        self.assertEqual((await a.get('/profile')).json()['nickname'], '旧昵称')
                        self.assertEqual((await b.get('/settings')).json()['preferences']['send_shortcut'], 'enter')
                        self.assertEqual((await b.post('/settings', json={'send_shortcut': 'ctrl_enter'})).status_code, 200)
                        self.assertEqual((await a.post('/settings', json={'send_shortcut': 'enter'})).status_code, 200)
                        self.assertEqual((await a.post('/history', json={'num': 0, 'front': True})).json()['history_messages'], [])
                        with pool.connection() as connection, connection.cursor() as cursor:
                            cursor.execute('SELECT COUNT(*) FROM abstract_messages')
                            self.assertEqual(cursor.fetchone()[0], 0)
                        waiting_a = asyncio.create_task(a.post('/chat', json={'user_input': 'wait for B'}))
                        try:
                            if not await asyncio.to_thread(started.wait, 8):
                                outcome = waiting_a.result().text if waiting_a.done() else 'request still running'
                                self.fail(f'first account did not reach its model: {outcome}')
                            reply_b = await asyncio.wait_for(b.post('/chat', json={'user_input': 'message B'}), 7)
                            self.assertEqual(reply_b.status_code, 200, reply_b.text)
                            self.assertFalse(waiting_a.done())
                        finally:
                            release.set()
                            reply_a = await waiting_a
                        self.assertEqual(reply_a.status_code, 200, reply_a.text)
                        self.assertTrue(reply_a.json()['saved'] and reply_b.json()['saved'])
                        self.assertEqual(reply_a.json()['conversation_round'], reply_b.json()['conversation_round'])
                        for client, message in ((a, 'wait for B'), (b, 'message B')):
                            rows = (await client.post('/history', json={'num': 0, 'front': True})).json()['history_messages']
                            label = 'test-model' if client is a else 'my-model'
                            self.assertEqual([(row['user'], row['assistant']) for row in rows], [(message, [f'{label}|||标记'])])
                        new_admin_password = 'NewAdmin!2026'
                        changed = await a.post('/auth/password', json={
                            'current_password': credential, 'new_password': new_admin_password,
                            'confirm_password': new_admin_password,
                        })
                        self.assertEqual(changed.status_code, 200, changed.text)
                        self.assertIsNone((await a.get('/auth/session')).json()['user'])
                        self.assertEqual((await b.get('/auth/session')).json()['user']['id'], login_b.json()['user']['id'])

                resumed_pool = open_pool(database, **db_options)
                self.addCleanup(resumed_pool.close)
                resumed = main.build_application(resumed_pool, profiles, folder)
                async with resumed.router.lifespan_context(resumed):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=resumed), base_url='http://testserver') as b:
                        self.assertEqual((await b.get('/auth/session')).json(), {'user': None})
                        self.assertEqual((await b.post('/auth/login', json={'username': 'Second_User', 'password': user_credential})).status_code, 200)
                        self.assertEqual((await b.get('/profile')).json()['nickname'], '第二个账号')
                        self.assertEqual((await b.get('/settings')).json()['preferences']['send_shortcut'], 'ctrl_enter')
                        self.assertEqual((await b.get('/settings')).json()['model']['model_name'], 'my-model')
                        self.assertEqual((await b.post('/history', json={'num': 0, 'front': True})).json()['history_messages'], [])
                        login_admin = await b.post('/auth/login', json={'username': 'admin', 'password': new_admin_password})
                        self.assertEqual(login_admin.status_code, 200, login_admin.text)
                        self.assertEqual((await b.get('/settings')).json()['preferences']['send_shortcut'], 'enter')
