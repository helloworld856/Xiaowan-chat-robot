"""账户资料与密码路由测试；所有变更都写入临时文件。"""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch

from test_auth import ROOT, auth_module, request


class ProfileHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth = auth_module()
        from core.app import create_app
        from profile_config import ProfileStore
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.credentials_path = Path(self.directory.name) / 'admin.json'
        self.credentials_path.write_bytes((ROOT / 'auth_config' / 'admin.json').read_bytes())
        self.now = 100.0
        self.service = self.auth.AuthService(self.credentials_path, clock=lambda: self.now)
        self.profile = ProfileStore(Path(self.directory.name) / 'user_data')
        self.profile_path = self.profile.path_for(1)
        self.app = create_app('test', auth_service=self.service, profile_store=self.profile)

    def login(self, password='123456'):
        status, headers, body = request(self.app, '/auth/login', 'POST',
                                        {'username': 'admin', 'password': password})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {'user': {'id': 1, 'username': 'admin', 'role': 'admin'}})
        jar = SimpleCookie()
        jar.load(headers['set-cookie'])
        return f'{self.auth.COOKIE_NAME}={jar[self.auth.COOKIE_NAME].value}'

    def test_profile_returns_default_admin_and_six_fixed_avatar_choices(self):
        status, headers, body = request(self.app, '/profile', cookie=self.login())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            'username': 'admin', 'nickname': 'admin', 'avatar_id': 'cat',
            'avatar_url': '/avatars/system/cat.png', 'default_avatar_id': 'cat',
            'avatars': [
                {'id': 'cat', 'name': '小猫', 'url': '/avatars/system/cat.png'},
                {'id': 'fox', 'name': '狐狸', 'url': '/avatars/system/fox.png'},
                {'id': 'rabbit', 'name': '兔子', 'url': '/avatars/system/rabbit.png'},
                {'id': 'bear', 'name': '小熊', 'url': '/avatars/system/bear.png'},
                {'id': 'penguin', 'name': '企鹅', 'url': '/avatars/system/penguin.png'},
                {'id': 'frog', 'name': '青蛙', 'url': '/avatars/system/frog.png'},
            ],
        })
        self.assertEqual(headers['cache-control'], 'no-store')

    def test_anonymous_profile_and_password_requests_are_rejected(self):
        for method, path in [('GET', '/profile'), ('POST', '/profile'), ('POST', '/auth/password')]:
            with self.subTest(method=method, path=path):
                self.assertEqual(request(self.app, path, method)[0], 401)
        self.assertFalse(self.profile_path.exists())

    def test_profile_save_trims_nickname_and_survives_store_restart(self):
        from profile_config import ProfileStore
        cookie = self.login()
        status, _, body = request(self.app, '/profile', 'POST',
                                  {'nickname': '  晚风  ', 'avatar_id': 'fox'}, cookie=cookie)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual((data['username'], data['nickname'], data['avatar_id'], data['avatar_url']),
                         ('admin', '晚风', 'fox', '/avatars/system/fox.png'))
        self.assertEqual(len(data['avatars']), 6)
        self.assertEqual(json.loads(self.profile_path.read_text(encoding='utf-8')),
                         {'nickname': '晚风', 'avatar_id': 'fox'})
        self.app.state.profile = ProfileStore(self.profile.root)
        self.assertEqual(json.loads(request(self.app, '/profile', cookie=cookie)[2]), data)
        self.assertEqual(json.loads(request(self.app, '/auth/session', cookie=cookie)[2]),
                         {'user': {'id': 1, 'username': 'admin', 'role': 'admin'}})

    def test_registered_account_has_its_own_profile_file_and_defaults(self):
        password = 'Second!2026'
        username = 'Second_User' + '1' * 21
        status, _, body = request(self.app, '/auth/register', 'POST', {
            'username': username, 'password': password, 'confirm_password': password,
        })
        self.assertEqual(status, 201, body)
        second_id = json.loads(body)['user']['id']
        token, _ = self.service.login(username, password, 'another-client')
        second_cookie = f'{self.auth.COOKIE_NAME}={token}'
        admin_cookie = self.login()
        self.assertEqual(json.loads(request(self.app, '/profile', cookie=second_cookie)[2])['nickname'], username)
        self.assertFalse(self.profile.path_for(second_id).exists())
        self.assertEqual(request(self.app, '/profile', 'POST', {'nickname': '晚风', 'avatar_id': 'bear'}, cookie=second_cookie)[0], 200)
        self.assertEqual(request(self.app, '/profile', 'POST', {'nickname': '管理', 'avatar_id': 'fox'}, cookie=admin_cookie)[0], 200)
        self.assertEqual(json.loads(self.profile.path_for(second_id).read_text(encoding='utf-8')), {'nickname': '晚风', 'avatar_id': 'bear'})
        self.assertEqual(json.loads(self.profile_path.read_text(encoding='utf-8')), {'nickname': '管理', 'avatar_id': 'fox'})

    def test_migration_preserves_newer_profile(self):
        from profile_config import Profile
        legacy = Path(self.directory.name) / 'profile.json'
        legacy.write_text('{"nickname":"旧昵称","avatar_id":"cat"}', encoding='utf-8')
        self.profile.import_legacy_profile(1, legacy)
        self.assertEqual(self.profile.get(1, 'admin').nickname, '旧昵称')
        self.profile.update(1, Profile(nickname='新昵称', avatar_id='fox'))
        self.profile.import_legacy_profile(1, legacy)
        self.assertEqual(self.profile.get(1, 'admin').nickname, '新昵称')

    def test_corrupt_profile_can_be_replaced_using_defaults(self):
        corrupt = b'{"avatar_id":'
        self.profile_path.parent.mkdir(parents=True)
        self.profile_path.write_bytes(corrupt)
        cookie = self.login()

        status, _, body = request(self.app, '/profile', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['avatar_id'], 'cat')
        self.assertEqual(self.profile_path.read_bytes(), corrupt)

        status, _, body = request(self.app, '/profile', 'POST',
                                  {'nickname': '新资料', 'avatar_id': 'fox'}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['avatar_id'], 'fox')

    def test_profile_rejects_invalid_nickname_avatar_and_extra_fields(self):
        cookie = self.login()
        invalid = [
            {'nickname': '', 'avatar_id': 'cat'}, {'nickname': ' \t\n ', 'avatar_id': 'cat'},
            {'nickname': '晚' * 33, 'avatar_id': 'cat'},
            {'nickname': 'admin', 'avatar_id': 'unknown'},
            {'nickname': 'admin', 'avatar_id': '../../admin.json'},
            {'nickname': 'admin', 'avatar_id': 'https://example.com/avatar.png'},
            {'nickname': 'admin', 'avatar_id': 'cat', 'username': 'other'},
            {'nickname': 'admin', 'avatar_id': 'cat', 'avatar_url': '/other.png'},
            {'nickname': 'admin'}, {'avatar_id': 'cat'}, {},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(request(self.app, '/profile', 'POST', payload, cookie=cookie)[0], 422)
        self.assertFalse(self.profile_path.exists())

    def test_profile_accepts_trimmed_32_character_boundary_and_all_system_avatars(self):
        cookie = self.login()
        for avatar_id in ['cat', 'fox', 'rabbit', 'bear', 'penguin', 'frog']:
            with self.subTest(avatar_id=avatar_id):
                status, _, body = request(self.app, '/profile', 'POST',
                    {'nickname': '  ' + '晚' * 32 + '  ', 'avatar_id': avatar_id}, cookie=cookie)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['nickname'], '晚' * 32)
                self.assertEqual(json.loads(body)['avatar_url'], f'/avatars/system/{avatar_id}.png')

    def test_profile_write_failure_preserves_saved_profile_and_removes_temporary_file(self):
        cookie = self.login()
        original = {'nickname': '原来的名字', 'avatar_id': 'cat'}
        self.profile_path.parent.mkdir(parents=True)
        self.profile_path.write_text(json.dumps(original), encoding='utf-8')
        with patch('os.replace', side_effect=OSError('private failure text')):
            status, _, body = request(self.app, '/profile', 'POST',
                                      {'nickname': '新名字', 'avatar_id': 'frog'}, cookie=cookie)
        self.assertEqual(status, 500)
        self.assertNotIn(b'private failure text', body)
        self.assertEqual(json.loads(self.profile_path.read_text(encoding='utf-8')), original)
        self.assertEqual(json.loads(request(self.app, '/profile', cookie=cookie)[2])['nickname'], '原来的名字')
        self.assertEqual({path.name for path in self.profile_path.parent.iterdir()}, {'profile.json'})

    def test_profile_writes_reject_cross_origin_and_avatar_upload_route_is_absent(self):
        cookie = self.login()
        self.assertEqual(request(self.app, '/profile', 'POST',
            {'nickname': 'admin', 'avatar_id': 'fox'}, cookie=cookie, origin='https://elsewhere.example')[0], 403)
        self.assertEqual(request(self.app, '/profile/avatar', 'POST', {}, cookie=cookie)[0], 404)

    def test_persona_uses_saved_system_avatar_without_changing_bot_fields(self):
        self.profile_path.parent.mkdir(parents=True)
        self.profile_path.write_text(json.dumps({'nickname': '晚风', 'avatar_id': 'penguin'}), encoding='utf-8')
        persona = types.SimpleNamespace(BOT_AVATAR='bot.png', BOT_NAME='小晚',
                                        BOT_BIRTHDAY='2000年1月1日', BOT_BIRTHPLACE='杭州')
        spec = importlib.util.spec_from_file_location('tested_profile_persona', ROOT / 'app' / 'routers' / 'persona.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'persona_config': types.SimpleNamespace(persona=persona)}):
            spec.loader.exec_module(module)
        self.app.include_router(module.router)
        cookie = self.login()
        status, _, body = request(self.app, '/persona', cookie=cookie)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data, {'BOT_AVATAR': 'http://testserver/avatars/bot.png', 'BOT_NAME': '小晚',
            'BOT_BIRTHDAY': '2000年1月1日', 'BOT_BIRTHPLACE': '杭州',
            'USER_AVATAR': 'http://testserver/avatars/system/penguin.png'})

    def test_password_change_persists_hash_revokes_all_sessions_and_requires_new_login(self):
        cookie, other_cookie = self.login(), self.login()
        original = self.credentials_path.read_bytes()
        status, headers, body = request(self.app, '/auth/password', 'POST',
            {'current_password': '123456', 'new_password': 'Change!2026', 'confirm_password': 'Change!2026'},
            cookie=cookie, scheme='https')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True})
        self.assertIn('Max-Age=0', headers['set-cookie'])
        self.assertIn('HttpOnly', headers['set-cookie'])
        self.assertIn('Secure', headers['set-cookie'])
        self.assertIn('SameSite=strict', headers['set-cookie'])
        for old_cookie in [cookie, other_cookie]:
            with self.subTest(cookie=old_cookie):
                self.assertEqual(request(self.app, '/profile', cookie=old_cookie)[0], 401)
                self.assertEqual(json.loads(request(self.app, '/auth/session', cookie=old_cookie)[2]), {'user': None})
        saved = self.credentials_path.read_bytes()
        self.assertNotEqual(saved, original)
        self.assertNotIn(b'Change!2026', saved)
        self.assertEqual(set(json.loads(saved)), {'username', 'password_hash'})
        self.assertTrue(json.loads(saved)['password_hash'].startswith('pbkdf2_sha256$600000$'))
        self.assertEqual(request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': '123456'})[0], 401)
        self.assertEqual(request(self.app, '/profile', cookie=self.login('Change!2026'))[0], 200)
        reloaded = self.auth.AuthService(self.credentials_path)
        with self.assertRaises(self.auth.InvalidCredentials):
            reloaded.login('admin', '123456', 'restart')
        token, _ = reloaded.login('admin', 'Change!2026', 'restart')
        self.assertEqual(reloaded.get_user(token).username, 'admin')

    def test_wrong_current_password_is_400_and_preserves_cookie_session_and_credentials(self):
        cookie = self.login()
        original = self.credentials_path.read_bytes()
        status, headers, _ = request(self.app, '/auth/password', 'POST',
            {'current_password': 'wrong', 'new_password': 'new-password-26', 'confirm_password': 'new-password-26'}, cookie=cookie)
        self.assertEqual(status, 400)
        self.assertNotIn('set-cookie', headers)
        self.assertEqual(request(self.app, '/profile', cookie=cookie)[0], 200)
        self.assertEqual(self.credentials_path.read_bytes(), original)

    def test_session_expiring_before_password_change_returns_401_and_preserves_credentials(self):
        cookie = self.login()
        original = self.credentials_path.read_bytes()
        with patch.object(self.service, 'clock', side_effect=(self.now, self.now + self.auth.SESSION_TTL_SECONDS)):
            status, _, _ = request(self.app, '/auth/password', 'POST', {
                'current_password': '123456', 'new_password': 'Change!2026', 'confirm_password': 'Change!2026',
            }, cookie=cookie)
        self.assertEqual(status, 401)
        self.assertEqual(self.credentials_path.read_bytes(), original)

    def test_password_validation_rejects_mismatch_same_password_invalid_lengths_and_extras(self):
        cookie = self.login()
        original = self.credentials_path.read_bytes()
        for new, confirmation, expected in [
            ('short', 'short', 422), ('a' * 16, 'a' * 16, 422),
            ('new-password-26', 'other-pass-26', 400), ('123456', '123456', 400),
        ]:
            with self.subTest(new_length=len(new), expected=expected):
                status, _, _ = request(self.app, '/auth/password', 'POST',
                    {'current_password': '123456', 'new_password': new, 'confirm_password': confirmation}, cookie=cookie)
                self.assertEqual(status, expected)
        self.assertEqual(request(self.app, '/auth/password', 'POST',
            {'current_password': '123456', 'new_password': 'new-password-26', 'confirm_password': 'new-password-26',
             'username': 'other'}, cookie=cookie)[0], 422)
        self.assertEqual(self.credentials_path.read_bytes(), original)
        self.assertEqual(request(self.app, '/profile', cookie=cookie)[0], 200)

    def test_password_change_accepts_15_character_password(self):
        cookie = self.login()
        status, _, _ = request(self.app, '/auth/password', 'POST',
            {'current_password': '123456', 'new_password': '密' * 15, 'confirm_password': '密' * 15}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(request(self.app, '/profile', cookie=self.login('密' * 15))[0], 200)

    def test_password_checks_share_login_attempt_limit_and_recover_after_one_minute(self):
        cookie = self.login()
        for _ in range(3):
            self.assertEqual(request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': 'wrong'})[0], 401)
        bad = {'current_password': 'wrong', 'new_password': 'new-password-26', 'confirm_password': 'new-password-26'}
        for _ in range(2):
            self.assertEqual(request(self.app, '/auth/password', 'POST', bad, cookie=cookie)[0], 400)
        good = {**bad, 'current_password': '123456'}
        status, headers, _ = request(self.app, '/auth/password', 'POST', good, cookie=cookie)
        self.assertEqual(status, 429)
        self.assertEqual(headers['retry-after'], '60')
        self.assertEqual(request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': '123456'})[0], 429)
        self.now += 61
        self.assertEqual(request(self.app, '/auth/password', 'POST', good, cookie=cookie)[0], 200)

    def test_password_save_failure_keeps_file_hash_all_sessions_and_hides_exception_text(self):
        cookie, other_cookie = self.login(), self.login()
        original = self.credentials_path.read_bytes()
        with patch('os.replace', side_effect=OSError('do not disclose current=123456 new=new-password')):
            status, headers, body = request(self.app, '/auth/password', 'POST',
                {'current_password': '123456', 'new_password': 'new-password-26', 'confirm_password': 'new-password-26'}, cookie=cookie)
        self.assertEqual(status, 500)
        self.assertNotIn(b'123456', body)
        self.assertNotIn(b'new-password', body)
        self.assertNotIn('set-cookie', headers)
        self.assertEqual(self.credentials_path.read_bytes(), original)
        for saved_cookie in [cookie, other_cookie]:
            self.assertEqual(request(self.app, '/profile', cookie=saved_cookie)[0], 200)
        self.assertEqual(request(self.app, '/profile', cookie=self.login())[0], 200)
        self.assertEqual(request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': 'new-password-26'})[0], 401)
        self.assertEqual({path.name for path in self.credentials_path.parent.iterdir()}, {'admin.json'})

    def test_password_write_rejects_cross_origin(self):
        cookie = self.login()
        original = self.credentials_path.read_bytes()
        self.assertEqual(request(self.app, '/auth/password', 'POST',
            {'current_password': '123456', 'new_password': 'new-password-26', 'confirm_password': 'new-password-26'},
            cookie=cookie, origin='https://elsewhere.example')[0], 403)
        self.assertEqual(self.credentials_path.read_bytes(), original)
