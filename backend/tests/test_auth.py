import importlib
import json
import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch

from _imports import BACKEND_ROOT as ROOT


def auth_module():
    try:
        return importlib.import_module('app.accounts.auth_service')
    except ModuleNotFoundError:
        raise AssertionError('账户验证尚未实现') from None


def request(app, path, method='GET', payload=None, cookie=None, origin=None, scheme='http', expected_user=None,
            extra_headers=()):
    """执行真实 ASGI 应用；这些路由没有外部异步 I/O，不需要网络套接字。"""
    headers = [(b'host', b'testserver'), (b'content-type', b'application/json')]
    headers.extend(extra_headers)
    if cookie:
        headers.append((b'cookie', cookie.encode()))
    if origin:
        headers.append((b'origin', origin.encode()))
    if expected_user is not None:
        headers.append((b'x-xiaowan-user', str(expected_user).encode()))
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
    async def run_locally(function, /, *args, **options):
        return function(*args)
    try:
        # Only replace ASGI's thread dispatch; exercise real route code in the socket-restricted test harness.
        with patch('anyio.to_thread.run_sync', new=run_locally):
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
        token, user = self.service.login('admin', '123456', 'test')
        self.assertEqual(user.username, 'admin')
        self.assertEqual(self.service.get_user(token), user)
        self.assertIsNone(self.service.get_user('admin'))
        self.assertIsNone(self.service.get_user('forged-token'))

    def test_sessions_expire_and_logout_revokes_them(self):
        token, _ = self.service.login('admin', '123456', 'test')
        self.service.logout(token)
        self.assertIsNone(self.service.get_user(token))
        token, _ = self.service.login('admin', '123456', 'test')
        self.now += 28800
        self.assertIsNone(self.service.get_user(token))

    def test_repeated_bad_logins_are_limited_and_recover(self):
        for _ in range(5):
            with self.assertRaises(self.auth.InvalidCredentials):
                self.service.login('admin', 'wrong', 'test')
        with self.assertRaises(self.auth.LoginThrottled):
            self.service.login('admin', '123456', 'test')
        self.now += 61
        token, _ = self.service.login('admin', '123456', 'test')
        self.assertEqual(self.service.get_user(token).username, 'admin')

    def test_configured_rate_limits_apply_per_client_and_expire(self):
        from app.system_config import AuthConfig

        config = AuthConfig(rate_limit={
            'login_attempts': 1, 'registration_attempts': 2, 'window_seconds': 7, 'max_clients': 1,
        })
        service = self.auth.AuthService(clock=lambda: self.now, config=config)
        with self.assertRaises(self.auth.InvalidCredentials):
            service.login('admin', 'wrong', 'first')
        for client in ('first', 'second'):
            with self.subTest(client=client), self.assertRaises(self.auth.LoginThrottled) as error:
                service.login('admin', '123456', client)
            self.assertEqual(error.exception.retry_after, 7)

        service.register('FirstUser', 'Reg!12', 'first')
        service.register('SecondUser', 'Reg!12', 'first')
        with self.assertRaises(self.auth.LoginThrottled):
            service.register('ThirdUser', 'Reg!12', 'first')
        self.now += 7
        self.assertEqual(service.login('admin', '123456', 'second')[1].username, 'admin')
        self.assertEqual(service.register('ThirdUser', 'Reg!12', 'second').username, 'ThirdUser')


class AuthHTTPTests(unittest.TestCase):
    DEFAULT_LIMITS = {'password_min_length': 6, 'password_max_length': 15,
                      'max_nickname_chars': 32, 'max_message_chars': 8000}

    def setUp(self):
        self.auth = auth_module()
        from app.application import create_app
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
        self.assertEqual(json.loads(body), {'user': {'id': 1, 'username': 'admin', 'role': 'admin'}})
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
                self.assertEqual(json.loads(body), {'user': None, 'limits': self.DEFAULT_LIMITS})
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
        self.assertEqual(json.loads(body), {'user': None, 'limits': self.DEFAULT_LIMITS})
        self.assertEqual(request(self.app, '/history', cookie=cookie)[0], 401)

    def test_wrong_password_and_expired_session_are_rejected(self):
        status, headers, _ = request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': 'wrong'})
        self.assertEqual(status, 401)
        self.assertNotIn('set-cookie', headers)
        _, cookie = self.login()
        self.now += 28800
        status, _, body = request(self.app, '/auth/session', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'user': None, 'limits': self.DEFAULT_LIMITS})
        self.assertEqual(request(self.app, '/chat', 'POST', cookie=cookie)[0], 401)

    def test_relogin_rotates_token_and_https_cookie_is_secure(self):
        _, old_cookie = self.login()
        headers, new_cookie = self.login(scheme='https', cookie=old_cookie)
        self.assertNotEqual(old_cookie, new_cookie)
        self.assertIn('Secure', headers['set-cookie'])
        status, _, body = request(self.app, '/auth/session', cookie=old_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'user': None, 'limits': self.DEFAULT_LIMITS})
        self.assertEqual(request(self.app, '/auth/session', cookie=new_cookie)[0], 200)

    def test_automatic_https_proxy_preserves_same_origin_and_secure_cookie(self):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        app = ProxyHeadersMiddleware(self.app, trusted_hosts='127.0.0.1')
        credentials = {'username': 'admin', 'password': '123456'}
        forwarded = ((b'x-forwarded-proto', b'https'),)
        status, headers, _ = request(app, '/auth/login', 'POST', credentials,
                                     origin='https://testserver', extra_headers=forwarded)
        self.assertEqual(status, 200)
        self.assertIn('Secure', headers['set-cookie'])
        self.assertEqual(request(app, '/auth/login', 'POST', credentials,
                                 origin='https://other.example', extra_headers=forwarded)[0], 403)

    def test_cross_origin_writes_are_rejected(self):
        self.assertEqual(request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': '123456'}, origin='https://elsewhere.example')[0], 403)
        self.assertEqual(request(self.app, '/auth/register', 'POST', {}, origin='https://elsewhere.example')[0], 403)
        _, cookie = self.login()
        self.assertEqual(request(self.app, '/settings', 'POST', cookie=cookie, origin='https://elsewhere.example')[0], 403)
        self.assertEqual(request(self.app, '/auth/logout', 'POST', cookie=cookie, origin='http://testserver')[0], 200)

    def test_registration_creates_only_an_ordinary_account_and_requires_separate_login(self):
        password = 'Reg!12'
        credentials = {'username': 'NewUser', 'password': password, 'confirm_password': password}
        status, headers, body = request(self.app, '/auth/register', 'POST', credentials)
        self.assertEqual(status, 201, body)
        user = json.loads(body)['user']
        self.assertEqual((user['username'], user['role']), ('NewUser', 'user'))
        self.assertNotEqual(user['id'], 1)
        self.assertNotIn('set-cookie', headers)
        self.assertEqual(request(self.app, '/auth/register', 'POST', {**credentials, 'username': 'newuser'})[0], 409)
        self.assertEqual(request(self.app, '/auth/register', 'POST', {**credentials, 'username': 'AdMiN'})[0], 409)
        status, headers, body = request(self.app, '/auth/login', 'POST', {'username': 'newuser', 'password': password})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {'user': user})
        cookie = headers['set-cookie'].split(';', 1)[0]
        self.assertEqual(json.loads(request(self.app, '/auth/session', cookie=cookie)[2]),
                         {'user': user, 'limits': self.DEFAULT_LIMITS})

    def test_configured_session_lifetime_applies_to_cookie_and_server(self):
        from app.application import create_app
        from app.system_config import AuthConfig

        service = self.auth.AuthService(clock=lambda: self.now, config=AuthConfig(session_ttl_seconds=7))
        self.app = create_app('test', auth_service=service)
        headers, cookie = self.login()
        jar = SimpleCookie(headers['set-cookie'])
        self.assertEqual(jar[self.auth.COOKIE_NAME]['max-age'], '7')
        self.now += 6
        self.assertEqual(json.loads(request(self.app, '/auth/session', cookie=cookie)[2])['user']['username'], 'admin')
        self.now += 1
        self.assertIsNone(json.loads(request(self.app, '/auth/session', cookie=cookie)[2])['user'])

    def test_session_exposes_only_the_configured_form_limits(self):
        from app.application import create_app
        from app.system_config import AuthConfig, SystemConfig

        auth = AuthConfig(password={'min_length': 8, 'max_length': 20})
        config = SystemConfig(profile={'max_nickname_chars': 12}, chat={'max_message_chars': 80})
        service = self.auth.AuthService(config=auth)
        app = create_app('test', auth_service=service, system_config=config)
        status, _, body = request(app, '/auth/session')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'user': None, 'limits': {
            'password_min_length': 8, 'password_max_length': 20, 'max_nickname_chars': 12, 'max_message_chars': 80,
        }})

    def test_configured_password_policy_applies_when_setting_a_password(self):
        from app.application import create_app
        from app.system_config import AuthConfig

        service = self.auth.AuthService(config=AuthConfig(password={'min_length': 8, 'max_length': 20}))
        self.app = create_app('test', auth_service=service)
        _, cookie = self.login()
        for password, error_type in (('synthet', 'string_too_short'), ('synthetic-password-2!', 'string_too_long')):
            with self.subTest(password_length=len(password)):
                status, _, body = request(self.app, '/auth/register', 'POST', {
                    'username': 'NewUser', 'password': password, 'confirm_password': password,
                })
                self.assertEqual(status, 422)
                details = json.loads(body)['detail']
                self.assertEqual([(item['loc'][-1], item['type']) for item in details],
                                 [('password', error_type), ('confirm_password', error_type)])
                self.assertNotIn(password.encode(), body)

        accepted = 'synthetic-password-1'
        self.assertEqual(request(self.app, '/auth/register', 'POST', {
            'username': 'NewUser', 'password': accepted, 'confirm_password': accepted,
        })[0], 201)
        change = {'current_password': '123456', 'new_password': accepted, 'confirm_password': accepted}
        self.assertEqual(request(self.app, '/auth/password', 'POST',
                                 {**change, 'new_password': 'short'}, cookie=cookie)[0], 422)
        self.assertEqual(request(self.app, '/auth/password', 'POST', change, cookie=cookie)[0], 200)
        self.assertEqual(request(self.app, '/auth/login', 'POST', {'username': 'admin', 'password': accepted})[0], 200)

    def test_registration_rejects_client_role_invalid_password_length_and_invalid_username(self):
        secret = 'Reg!12'
        credentials = {'username': 'Valid_User', 'password': secret, 'confirm_password': secret}
        self.assertEqual(request(self.app, '/auth/register', 'POST', {**credentials, 'role': 'admin'})[0], 422)
        self.assertEqual(request(self.app, '/auth/register', 'POST', {**credentials, 'username': 'bad-name'})[0], 422)
        self.assertEqual(request(self.app, '/auth/register', 'POST', {**credentials, 'password': 'short'})[0], 422)
        self.assertEqual(request(self.app, '/auth/register', 'POST', {
            **credentials, 'password': '密' * 16, 'confirm_password': '密' * 16,
        })[0], 422)
        self.assertEqual(request(self.app, '/auth/register', 'POST', {**credentials, 'confirm_password': 'Mismatch!12'})[0], 400)

    def test_auth_validation_errors_only_return_field_diagnostics(self):
        _, cookie = self.login()
        sample = 'synthetic' * 30
        cases = (
            ('/auth/login', {'username': 'sample', 'password': sample}, 'password'),
            ('/auth/register', {'username': 'sample', 'password': sample, 'confirm_password': sample}, 'password'),
            ('/auth/password', {'current_password': sample, 'new_password': sample, 'confirm_password': sample},
             'current_password'),
            ('/auth/login', sample, None),
            ('/auth/login', {'password': sample}, 'username'),
            ('/auth/login', {'username': 'sample', 'password': 'sample', sample: True}, None),
        )
        for path, payload, field in cases:
            with self.subTest(path=path, field=field):
                status, headers, body = request(self.app, path, 'POST', payload, cookie=cookie)
                self.assertEqual(status, 422)
                self.assertEqual(headers['cache-control'], 'no-store')
                errors = json.loads(body)['detail']
                expected = ['body', field] if field else ['body']
                self.assertIn(expected, [error['loc'] for error in errors])
                for error in errors:
                    self.assertEqual(set(error), {'type', 'loc', 'msg'})
                self.assertFalse(sample.encode() in body)

    # 旧标签页仍带原账号头，但浏览器已共享新账号的 Cookie。
    def test_stale_tab_cannot_mutate_new_cookie_account_or_log_it_out(self):
        _, admin_cookie = self.login()
        second = self.service.register('OtherUser', 'Reg!12', 'other-client')
        for path in ['/settings', '/auth/logout']:
            self.assertEqual(request(self.app, path, 'POST', cookie=admin_cookie, expected_user=second.id)[0], 401)
        self.assertEqual(self.business_calls, [])
        # Session discovery still sees the real Cookie, and matching requests go through.
        status, _, body = request(self.app, '/auth/session', cookie=admin_cookie, expected_user=second.id)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['user']['username'], 'admin')
        self.assertEqual(request(self.app, '/settings', 'POST', cookie=admin_cookie, expected_user=1)[0], 200)

    def test_password_change_revokes_two_sessions_for_only_one_account(self):
        old = 'Reg!12'
        new = 'Change!56'
        self.assertEqual(request(self.app, '/auth/register', 'POST',
            {'username': 'Alice', 'password': old, 'confirm_password': old})[0], 201)
        alice = [self.service.login('alice', old, 'test')[0] for _ in range(2)]
        _, admin_cookie = self.login()
        status, _, _ = request(self.app, '/auth/password', 'POST',
            {'current_password': old, 'new_password': new, 'confirm_password': new},
            cookie=f'{self.auth.COOKIE_NAME}={alice[0]}')
        self.assertEqual(status, 200)
        for token in alice:
            self.assertIsNone(self.service.get_user(token))
        self.assertEqual(json.loads(request(self.app, '/auth/session', cookie=admin_cookie)[2])['user']['role'], 'admin')
        with self.assertRaises(self.auth.InvalidCredentials):
            self.service.login('alice', old, 'test')
        self.assertEqual(self.service.login('alice', new, 'test')[1].username, 'Alice')
