"""Exercise SQL persistence with an isolated database and MySQL parameter syntax adapter."""

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pymysql

from _imports import fixture_admin_hash
from test_auth import request
from app.accounts.repository import AccountStorageError, DuplicateUsername, SQLAccountStore
from app.accounts.auth_service import AuthService, COOKIE_NAME, InvalidCredentials, hash_password


class _Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cursor.close()

    def execute(self, sql, parameters=()):
        try:
            return self.cursor.execute(sql.replace('%s', '?'), parameters)
        except sqlite3.IntegrityError as error:
            if 'UNIQUE constraint failed: users.username_key' in str(error):
                raise pymysql.IntegrityError(1062, 'duplicate username') from None
            raise

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class _Connection:
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return _Cursor(self.connection.cursor())

    def commit(self):
        self.connection.commit()


class _Pool:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path)
        try:
            yield _Connection(connection)
        finally:
            connection.close()


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(directory.cleanup)
        self.pool = _Pool(Path(directory.name) / 'accounts.sqlite')
        with self.pool.connection() as db, db.cursor() as cursor:
            cursor.execute('''CREATE TABLE users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL
            )''')
            db.commit()
        self.root = Path(directory.name)

    def test_admin_initialization_preserves_new_password_when_setup_runs_again(self):
        old_password = '旧版密码' * 32
        new_password = '🔐' * 15
        store = SQLAccountStore(self.pool)
        self.assertIsNone(store.get_admin())
        admin = store.initialize_admin(hash_password(old_password))
        self.assertEqual(store.get_admin(), admin)
        service = AuthService(account_store=store)
        from app.application import create_app
        app = create_app('test', auth_service=service)
        status, headers, body = request(app, '/auth/login', 'POST', {'username': 'admin', 'password': old_password})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['user']['id'], admin.id)
        cookie = headers['set-cookie'].split(';', 1)[0]
        status, _, _ = request(app, '/auth/password', 'POST', {
            'current_password': old_password, 'new_password': new_password, 'confirm_password': new_password,
        }, cookie=cookie)
        self.assertEqual(status, 200)
        restarted_store = SQLAccountStore(self.pool)
        self.assertEqual(restarted_store.initialize_admin(hash_password(old_password)), admin)
        self.assertEqual(restarted_store.get_admin(), admin)
        restarted_service = AuthService(account_store=restarted_store)
        self.assertEqual(restarted_service.login('admin', new_password, 'cli')[1], admin)
        with self.assertRaises(InvalidCredentials):
            restarted_service.login('admin', old_password, 'cli')

    def test_simultaneous_registration_persists_one_case_insensitive_username(self):
        store = SQLAccountStore(self.pool)
        service = AuthService(account_store=store)
        barrier = Barrier(2)

        def register(username):
            barrier.wait(timeout=5)
            try:
                return service.register(username, 'Reg!12', 'cli')
            except DuplicateUsername:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(register, ('NewUser', 'newuser')))
        winner, = (item for item in results if item is not None)
        restored = AuthService(account_store=SQLAccountStore(self.pool))
        self.assertEqual(restored.login('NEWUSER', 'Reg!12', 'cli')[1], winner)
        with self.pool.connection() as db, db.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM users')
            self.assertEqual(cursor.fetchone(), (1,))

    def test_registration_reserves_admin_even_before_admin_is_seeded(self):
        from app.application import create_app

        store = SQLAccountStore(self.pool)
        app = create_app('test', auth_service=AuthService(account_store=store))
        for name in ('admin', 'ADMIN', 'AdMiN'):
            status, _, body = request(app, '/auth/register', 'POST', {
                'username': name, 'password': 'Reg!12', 'confirm_password': 'Reg!12',
            })
            self.assertEqual(status, 409)
            self.assertIn('保留', json.loads(body)['detail'])
        self.assertIsNone(store.find('admin'))

    def test_config_write_failure_rolls_back_registration(self):
        def failing_prepare(_):
            raise OSError('write failed')

        store = SQLAccountStore(self.pool, prepare_user=failing_prepare)
        with self.assertRaises(AccountStorageError):
            store.register('member', 'test credential hash')
        self.assertIsNone(store.find('member'))
        restored = SQLAccountStore(self.pool)
        self.assertEqual(restored.register('member', 'test credential hash').username, 'member')

    # 模拟数据库已经提交，但客户端没收到响应的情况。
    def test_registration_keeps_directory_when_commit_succeeded_without_ack(self):
        from app.accounts.profile import ProfileStore
        from app.accounts.preferences import UserPreferencesStore
        from app.accounts.directory import UserDirectoryStore

        directories = UserDirectoryStore(ProfileStore(self.root / 'users'), UserPreferencesStore(self.pool))
        store = SQLAccountStore(self.pool, prepare_user=directories.prepare_new)
        commit = _Connection.commit

        def lose_commit_ack(connection):
            commit(connection)
            raise pymysql.OperationalError(2013, 'synthetic commit acknowledgement loss')

        with patch.object(_Connection, 'commit', lose_commit_ack):
            with self.assertRaises(AccountStorageError):
                store.register('member', 'test credential hash')
        user = store.find('member').user
        self.assertEqual(set(entry.name for entry in directories.paths.directory(user.id).iterdir()),
                         {'profile.json', 'model.json', 'preferences.json', 'logs'})

    def test_lost_commit_response_requires_relogin_and_revokes_only_target_account(self):
        from app.application import create_app

        store = SQLAccountStore(self.pool)
        admin = store.initialize_admin(fixture_admin_hash())
        service = AuthService(account_store=store)
        app = create_app('test', auth_service=service)
        admin_tokens = [service.login('admin', '123456', 'cli')[0] for _ in range(2)]
        service.register('AnotherUser', 'Reg!12', 'other-cli')
        other_token, other = service.login('AnotherUser', 'Reg!12', 'other-cli')
        new_password = 'AdminNew!22'

        def commit_then_disconnect(connection):
            connection.connection.commit()
            raise pymysql.OperationalError(2013, 'connection lost after commit')

        with patch.object(_Connection, 'commit', new=commit_then_disconnect):
            status, headers, body = request(app, '/auth/password', 'POST', {
                'current_password': '123456', 'new_password': new_password, 'confirm_password': new_password,
            }, cookie=f'{COOKIE_NAME}={admin_tokens[0]}')
        self.assertEqual(status, 401, body)
        self.assertIn('无法确认', json.loads(body)['detail'])
        self.assertIn('Max-Age=0', headers['set-cookie'])
        for token in admin_tokens:
            self.assertIsNone(service.get_user(token))
        self.assertEqual(service.get_user(other_token), other)
        self.assertEqual(service.login('admin', new_password, 'cli')[1], admin)

    def test_update_rejected_before_commit_preserves_target_sessions(self):
        from app.application import create_app

        store = SQLAccountStore(self.pool)
        admin = store.initialize_admin(fixture_admin_hash())
        service = AuthService(account_store=store)
        app = create_app('test', auth_service=service)
        token, _ = service.login('admin', '123456', 'cli')
        execute = _Cursor.execute

        def reject_update(cursor, sql, parameters=()):
            if sql.startswith('UPDATE users'):
                raise pymysql.OperationalError(2013, 'connection lost before submit')
            return execute(cursor, sql, parameters)

        with patch.object(_Cursor, 'execute', new=reject_update):
            status, headers, body = request(app, '/auth/password', 'POST', {
                'current_password': '123456', 'new_password': 'AdminNew!22', 'confirm_password': 'AdminNew!22',
            }, cookie=f'{COOKIE_NAME}={token}')
        self.assertEqual(status, 500, body)
        self.assertNotIn('set-cookie', headers)
        self.assertEqual(service.get_user(token), admin)
        self.assertEqual(service.login('admin', '123456', 'cli')[1], admin)
