"""Optional MySQL 8 integration test. Only a randomly named test database is changed."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from _imports import BACKEND_ROOT as ROOT


@unittest.skipUnless(os.getenv('XIAOWAN_TEST_MYSQL_PORT'), 'requires an isolated MySQL instance')
class MultiUserMySQLTest(unittest.IsolatedAsyncioTestCase):
    def make_database(self):
        import pymysql

        from app.database.mysql import open_pool

        # 每个用例创建独立测试库，结束时由测试清理。
        database = f'xiaowan_test_{uuid4().hex}'
        db_options = {'host': '127.0.0.1', 'port': int(os.environ['XIAOWAN_TEST_MYSQL_PORT']),
                      'user': 'root', 'password': os.getenv('XIAOWAN_TEST_MYSQL_PASSWORD', '')}

        def drop_test_database():
            with pymysql.connect(**db_options) as connection, connection.cursor() as cursor:
                cursor.execute(f'DROP DATABASE IF EXISTS `{database}`')

        self.addCleanup(drop_test_database)
        pool = open_pool(database, **db_options)
        self.addCleanup(pool.close)
        return pool, database, db_options

    def test_compression_summarizes_oldest_rows_outside_the_cache(self):
        from app.chat.memory import MemoryManager, load_history
        from app.database.mysql import initialize_schema

        pool, _, _ = self.make_database()
        initialize_schema(pool)
        prompts = []

        def summarize(request):
            prompts.append(request['messages'][0]['content'])
            return {'messages': [SimpleNamespace(content='最早两轮的摘要')]}

        with (
            patch('app.chat.memory.HISTORY_CACHE_LIMIT', 4),
            patch('app.chat.memory.HISTORY_COMPRESSION_BATCH_SIZE', 2),
        ):
            account = MemoryManager(1, pool.connection)
            other = MemoryManager(2, pool.connection)
            for round_id in range(6):
                self.assertTrue(account.record_message({
                    'conversation_round': round_id, 'user': f'private-{round_id}', 'assistant': ['回复'],
                }))
            self.assertTrue(other.record_message({
                'conversation_round': 0, 'user': 'another account', 'assistant': ['回复'],
            }))

            account = MemoryManager(1, pool.connection)
            self.assertTrue(account.has_older_history)
            self.assertEqual([row['conversation_round'] for row in account.history_cache], [2, 3, 4, 5])
            account.compress_history(SimpleNamespace(invoke=summarize))

            self.assertIn('private-0', prompts[0])
            self.assertIn('private-1', prompts[0])
            self.assertNotIn('private-2', prompts[0])
            self.assertNotIn('another account', prompts[0])
            self.assertFalse(account.has_older_history)
            self.assertEqual(account.next_round, 6)
            self.assertEqual(MemoryManager(1, pool.connection).abstract_cache, '最早两轮的摘要')
            self.assertEqual(load_history(pool.connection, 1, 0), account.history_cache)
            self.assertEqual(load_history(pool.connection, 2, 0), other.history_cache)

    async def test_corrupt_legacy_profile_does_not_prevent_startup(self):
        import httpx

        from app import application as main
        from app.accounts.auth_service import hash_password
        from app.accounts.profile import ProfileStore
        from app.accounts.repository import SQLAccountStore
        from app.database.mysql import initialize_schema
        from app.llm.admin_settings import SettingsStore

        pool, _, _ = self.make_database()
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            folder = Path(directory)
            password = 'test configuration'
            initialize_schema(pool)
            SQLAccountStore(pool).initialize_admin(hash_password(password))
            (folder / 'profile.json').write_text('{invalid', encoding='utf-8')
            profiles = ProfileStore(folder / 'profiles')
            with patch.object(main, 'settings_store', SettingsStore(folder / 'settings.json')):
                app = main.build_application(pool, profiles, folder, config_root=folder)
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                        login = await client.post('/auth/login', json={'username': 'admin', 'password': password})
                        self.assertEqual(login.status_code, 200)
                        self.assertEqual((await client.get('/profile')).json()['nickname'], 'admin')

    def test_existing_account_preferences_migrate_on_first_read(self):
        from app.accounts.repository import SQLAccountStore
        from app.accounts.auth_service import hash_password
        from app.database.mysql import initialize_schema
        from app.accounts.profile import Profile, ProfileStore
        from app.accounts.preferences import Preferences, UserPreferencesStore
        from app.accounts.directory import UserDirectoryStore

        pool, _, _ = self.make_database()
        initialize_schema(pool)
        accounts = SQLAccountStore(pool)
        user = accounts.register('Legacy_User', hash_password('test only'))
        legacy = UserPreferencesStore(pool)
        legacy.import_legacy(user.id, Preferences(send_shortcut='ctrl_enter'))
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            profiles = ProfileStore(Path(directory) / 'profiles')
            data = UserDirectoryStore(profiles, legacy)
            profiles.update(user.id, Profile(nickname='旧资料', avatar_id='fox'))
            before = profiles.path_for(user.id).read_bytes()
            self.assertFalse(data.preferences.path_for(user.id).exists())

            self.assertEqual(data.preferences.get(user.id).send_shortcut, 'ctrl_enter')
            self.assertTrue(data.preferences.path_for(user.id).exists())
            self.assertEqual(profiles.path_for(user.id).read_bytes(), before)
            data.preferences.update(user.id, Preferences())
            self.assertEqual(data.preferences.get(user.id).send_shortcut, 'enter')
            self.assertEqual(legacy.find(user.id).send_shortcut, 'ctrl_enter')

    async def test_migration_account_isolation_concurrent_graphs_and_restart(self):
        import httpx

        from app.llm import factory as model_factory
        from app.llm.model_catalog import default_model_catalog
        from app import application as main
        from app.accounts.auth_service import hash_password
        from app.accounts.repository import SQLAccountStore
        from app.database.mysql import initialize_schema, open_pool
        from app.accounts.profile import ProfileStore
        from app.llm.admin_settings import SettingsStore

        pool, database, db_options = self.make_database()
        with pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute('CREATE TABLE history_messages (conversation_round INT PRIMARY KEY, user MEDIUMTEXT, assistant MEDIUMTEXT) ENGINE=MyISAM')
            cursor.execute('CREATE TABLE abstract_messages (abstract MEDIUMTEXT) ENGINE=MyISAM')
            cursor.execute("INSERT INTO history_messages VALUES (0, 'legacy history', 'legacy reply')")
            cursor.execute("INSERT INTO abstract_messages VALUES ('legacy summary')")
            connection.commit()

        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            folder = Path(directory)
            credential = 'test admin password'
            initialize_schema(pool)
            SQLAccountStore(pool).initialize_admin(hash_password(credential))
            (folder / 'profile.json').write_text('{"nickname":"旧昵称","avatar_id":"frog"}', encoding='utf-8')
            profiles = ProfileStore(folder / 'profiles')
            model_settings = SettingsStore(folder / 'settings.json')
            catalog = default_model_catalog()
            catalog.providers['deepseek'].models.extend(['test-model', 'my-model'])
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
                return response(json.dumps({'reply': [f'{label}|||标记', '第二条回复']}, ensure_ascii=False))

            admin_key = 'admin-only-placeholder'
            personal_key = 'personal-placeholder'

            def create_agents(merchant, key, name, *, base_url, adapter, public_only=False):
                self.assertEqual(base_url, 'https://api.deepseek.com')
                self.assertIn(key, (admin_key, personal_key))
                self.assertEqual(adapter, 'openai')
                return (SimpleNamespace(invoke=analyze), SimpleNamespace(invoke=lambda _: response('summary')),
                        SimpleNamespace(invoke=lambda payload, *, config: reply(payload, name)))

            with patch.object(main, 'settings_store', model_settings), \
                    patch.dict(os.environ, {'DEEPSEEK_API_KEY': admin_key}), \
                    patch.object(model_factory, 'create_agents', side_effect=create_agents) as factory, \
                    patch.object(model_factory, 'create_model', return_value=SimpleNamespace(invoke=lambda _: None)) as validator:
                app = main.build_application(pool, profiles, folder, config_root=folder, model_catalog=catalog)
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
                        winner = first if first.status_code == 201 else second
                        personal_folder = profiles.root / str(winner.json()['user']['id'])
                        self.assertEqual({path.name for path in personal_folder.iterdir()},
                                         {'model.json', 'profile.json', 'preferences.json', 'logs'})
                        login_b = await b.post('/auth/login', json={'username': 'second_user', 'password': user_credential})
                        self.assertEqual(login_b.status_code, 200, login_b.text)
                        self.assertEqual(login_b.json()['user']['role'], 'user')
                        personal_file = profiles.root / str(login_b.json()['user']['id']) / 'model.json'
                        self.assertIsNone(json.loads(personal_file.read_text(encoding='utf-8'))['active_provider'])
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
                        invalid = await b.post('/model/personal', json={**personal, 'base_url': 'https://127.0.0.1/v1'})
                        self.assertEqual(invalid.status_code, 200)
                        self.assertFalse(invalid.json()['status'])
                        self.assertNotIn(personal_key, invalid.text)
                        test = await b.post('/model/personal/test', json=personal)
                        self.assertTrue(test.json()['status'], test.text)
                        self.assertTrue((await a.post('/model/test', json={**options, 'model_name': 'test-model'})).json()['status'])
                        self.assertIsNone(json.loads(personal_file.read_text(encoding='utf-8'))['active_provider'])
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
                        for client, http_reply, message in ((a, reply_a, 'wait for B'), (b, reply_b, 'message B')):
                            rows = (await client.post('/history', json={'num': 0, 'front': True})).json()['history_messages']
                            label = 'test-model' if client is a else 'my-model'
                            expected = [f'{label}|||标记', '第二条回复']
                            self.assertEqual(http_reply.json()['response'], expected)
                            self.assertEqual([(row['user'], row['assistant']) for row in rows], [(message, expected)])
                        new_admin_password = 'NewAdmin!2026'
                        changed = await a.post('/auth/password', json={
                            'current_password': credential, 'new_password': new_admin_password,
                            'confirm_password': new_admin_password,
                        })
                        self.assertEqual(changed.status_code, 200, changed.text)
                        self.assertIsNone((await a.get('/auth/session')).json()['user'])
                        self.assertEqual((await b.get('/auth/session')).json()['user']['id'], login_b.json()['user']['id'])

                admin_log = (profiles.root / str(admin_info['id']) / 'logs' / 'app.log').read_text(encoding='utf-8')
                personal_log = (personal_folder / 'logs' / 'app.log').read_text(encoding='utf-8')
                self.assertIn('POST /model/test 返回 200', admin_log)
                self.assertIn('POST /model/personal/test 返回 200', personal_log)
                self.assertNotIn('POST /model/personal/test', admin_log)
                self.assertNotIn('POST /model/test 返回 200', personal_log)
                self.assertIn('POST /model/test 返回 403', personal_log)
                self.assertEqual(admin_log.count('模型连接测试完成'), 1)
                self.assertEqual(personal_log.count('模型连接测试完成'), 2)
                for contents in (admin_log, personal_log):
                    self.assertIn('模型连接测试完成', contents)
                    self.assertIn('回复 2 条', contents)
                    for secret in (admin_key, personal_key, 'wait for B', 'message B'):
                        self.assertNotIn(secret, contents)

                resumed_pool = open_pool(database, **db_options)
                self.addCleanup(resumed_pool.close)
                resumed = main.build_application(resumed_pool, profiles, folder, config_root=folder,
                                                 model_catalog=catalog)
                async with resumed.router.lifespan_context(resumed):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=resumed), base_url='http://testserver') as b:
                        self.assertIsNone((await b.get('/auth/session')).json()['user'])
                        self.assertEqual((await b.post('/auth/login', json={'username': 'Second_User', 'password': user_credential})).status_code, 200)
                        self.assertEqual((await b.get('/profile')).json()['nickname'], '第二个账号')
                        self.assertEqual((await b.get('/settings')).json()['preferences']['send_shortcut'], 'ctrl_enter')
                        self.assertEqual((await b.get('/settings')).json()['model']['model_name'], 'my-model')
                        self.assertEqual((await b.post('/history', json={'num': 0, 'front': True})).json()['history_messages'], [])
                        login_admin = await b.post('/auth/login', json={'username': 'admin', 'password': new_admin_password})
                        self.assertEqual(login_admin.status_code, 200, login_admin.text)
                        self.assertEqual((await b.get('/settings')).json()['preferences']['send_shortcut'], 'enter')
