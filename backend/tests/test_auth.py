import importlib
import json
import sys
import unittest
from http.cookies import SimpleCookie
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'app'))


def auth_module():
    try:
        return importlib.import_module('auth_config')
    except ModuleNotFoundError:
        raise AssertionError('账户验证尚未实现') from None


def request(app, path, method='GET', payload=None, cookie=None, origin=None, scheme='http'):
    """执行真实 ASGI 应用；这些路由没有外部异步 I/O，不需要网络套接字。"""
    headers = [(b'host', b'testserver'), (b'content-type', b'application/json')]
    if cookie:
        headers.append((b'cookie', cookie.encode()))
    if origin:
        headers.append((b'origin', origin.encode()))
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
             'http_version': '1.1', 'method': method, 'scheme': scheme, 'path': path,
             'raw_path': path.encode(), 'root_path': '', 'query_string': b'',
             'headers': headers, 'server': ('testserver', 80), 'client': ('127.0.0.1', 12345)}
    sent = []
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(payload).encode() if payload is not None else b'', 'more_body': False}
    async def send(message):
        sent.append(message)
    coroutine = app(scope, receive, send)
    try:
        coroutine.send(None)
    except StopIteration:
        pass
    else:
        coroutine.close()
        raise AssertionError('测试路由意外依赖外部异步 I/O')
    start = next(message for message in sent if message['type'] == 'http.response.start')
    body = b''.join(message.get('body', b'') for message in sent if message['type'] == 'http.response.body')
    return start['status'], {k.decode(): v.decode() for k, v in start['headers']}, body


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = auth_module()
        self.now = 100.0
        self.service = self.auth.AuthService(clock=lambda: self.now)

    def test_only_admin_with_correct_password_can_create_session(self):
        with self.assertRaises(self.auth.InvalidCredentials):
            self.service.login('someone', '123456', 'test')
        with self.assertRaises(self.auth.InvalidCredentials):
            self.service.login('admin', 'wrong', 'test')
        token = self.service.login('admin', '123456', 'test')
        self.assertEqual(self.service.get_user(token), 'admin')
        self.assertIsNone(self.service.get_user('admin'))
        self.assertIsNone(self.service.get_user('forged-token'))

    def test_sessions_expire_and_logout_revokes_them(self):
        token = self.service.login('admin', '123456', 'test')
        self.service.logout(token)
        self.assertIsNone(self.service.get_user(token))
        token = self.service.login('admin', '123456', 'test')
        self.now += self.auth.SESSION_TTL_SECONDS + 1
        self.assertIsNone(self.service.get_user(token))

    def test_repeated_bad_logins_are_limited_and_recover(self):
        for _ in range(5):
            with self.assertRaises(self.auth.InvalidCredentials):
                self.service.login('admin', 'wrong', 'test')
        with self.assertRaises(self.auth.LoginThrottled):
            self.service.login('admin', '123456', 'test')
        self.now += 61
        self.assertEqual(self.service.get_user(self.service.login('admin', '123456', 'test')), 'admin')


class AuthHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth = auth_module()
        from core.app import create_app
        self.now = 100.0
        self.service = self.auth.AuthService(clock=lambda: self.now)
        self.app = create_app('test', auth_service=self.service)
        self.business_calls = []
        async def business():
            self.business_calls.append('called')
            return {'ok': True}
        for path in ['/chat', '/history', '/settings', '/model', '/model/test', '/persona', '/version']:
            self.app.add_api_route(path, business, methods=['GET', 'POST'])
        async def homepage():
            return {'login': True}
        self.app.add_api_route('/', homepage)

    def login(self, scheme='http', cookie=None):
        status, headers, body = request(self.app, '/auth/login', 'POST',
                                        {'username': 'admin', 'password': '123456'}, cookie=cookie, scheme=scheme)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'user': {'username': 'admin'}})
        jar = SimpleCookie()
        jar.load(headers['set-cookie'])
        return headers, f'{self.auth.COOKIE_NAME}={jar[self.auth.COOKIE_NAME].value}'

    def test_login_page_is_public_but_all_business_apis_are_protected(self):
        self.assertEqual(request(self.app, '/')[0], 200)
        for path in ['/chat', '/history', '/settings', '/model', '/model/test', '/persona', '/version', '/openapi.json']:
            with self.subTest(path=path):
                self.assertEqual(request(self.app, path, 'POST' if path in ['/chat', '/model/test'] else 'GET')[0], 401)
        self.assertEqual(self.business_calls, [])

    def test_session_probe_returns_normal_signed_out_state_without_valid_cookie(self):
        for cookie in (None, f'{self.auth.COOKIE_NAME}=' + 'x' * 43):
            with self.subTest(cookie=cookie):
                status, headers, body = request(self.app, '/auth/session', cookie=cookie)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {'user': None})
                self.assertIn('no-store', headers['cache-control'])

    def test_login_cookie_refresh_session_logout_and_old_token_rejection(self):
        headers, cookie = self.login()
        self.assertIn('HttpOnly', headers['set-cookie'])
        self.assertIn('SameSite=strict', headers['set-cookie'])
        self.assertIn('no-store', headers['cache-control'])
        self.assertEqual(request(self.app, '/auth/session', cookie=cookie)[0], 200)
        self.assertEqual(request(self.app, '/settings', cookie=cookie)[0], 200)
        status, logout_headers, _ = request(self.app, '/auth/logout', 'POST', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=0', logout_headers['set-cookie'])
        status, _, body = request(self.app, '/auth/session', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'user': None})
        self.assertEqual(request(self.app, '/history', cookie=cookie)[0], 401)

    def test_wrong_password_and_expired_session_are_rejected(self):
        status, headers, _ = request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': 'wrong'})
        self.assertEqual(status, 401)
        self.assertNotIn('set-cookie', headers)
        _, cookie = self.login()
        self.now += self.auth.SESSION_TTL_SECONDS + 1
        status, _, body = request(self.app, '/auth/session', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'user': None})
        self.assertEqual(request(self.app, '/chat', 'POST', cookie=cookie)[0], 401)

    def test_relogin_rotates_token_and_https_cookie_is_secure(self):
        _, old_cookie = self.login()
        headers, new_cookie = self.login(scheme='https', cookie=old_cookie)
        self.assertNotEqual(old_cookie, new_cookie)
        self.assertIn('Secure', headers['set-cookie'])
        status, _, body = request(self.app, '/auth/session', cookie=old_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'user': None})
        self.assertEqual(request(self.app, '/auth/session', cookie=new_cookie)[0], 200)

    def test_cross_origin_writes_are_rejected_and_registration_is_absent(self):
        self.assertEqual(request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': '123456'}, origin='https://elsewhere.example')[0], 403)
        _, cookie = self.login()
        self.assertEqual(request(self.app, '/settings', 'POST', cookie=cookie, origin='https://elsewhere.example')[0], 403)
        self.assertEqual(request(self.app, '/auth/logout', 'POST', cookie=cookie, origin='http://testserver')[0], 200)
        _, cookie = self.login()
        self.assertEqual(request(self.app, '/auth/register', 'POST', cookie=cookie)[0], 404)
