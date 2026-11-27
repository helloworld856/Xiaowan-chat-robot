from collections import Counter
from ipaddress import ip_address
from logging import ERROR, WARNING
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from app.accounts.auth_service import AuthService, COOKIE_NAME
from app.account_logging import account_log, account_log_context, log_event, logger, request_log_context
from app.accounts.files import UserDataPaths


PAGE_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
HTTP_METHODS = frozenset(('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'CONNECT', 'TRACE'))
PUBLIC_AUTH_ROUTES = frozenset((('POST', '/auth/login'), ('POST', '/auth/logout'), ('POST', '/auth/register'),
                                ('GET', '/auth/session')))
AUTH_PATHS = frozenset(path for _, path in PUBLIC_AUTH_ROUTES) | {'/auth/account', '/auth/password'}


def origin_key(value: str):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid origin')
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def request_client_ip(scope):
    client = scope.get('client')
    if client:
        try:
            return ip_address(client[0].split('%', 1)[0])
        except (AttributeError, ValueError):
            pass
    return None


class AuthenticationMiddleware:
    def __init__(self, app, service: AuthService, user_data_paths: UserDataPaths | None = None):
        self.app = app
        self.service = service
        self.user_data_paths = user_data_paths
        self.active: Counter[int] = Counter()
        self.deleting: set[int] = set()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path, method = scope['path'], scope['method']
        log_method = method if method in HTTP_METHODS else 'OTHER'
        public_static = method in ('GET', 'HEAD') and (
            path in ('/', '/index.html', '/favicon.ico') or path.startswith(('/assets/', '/ico/', '/avatars/')))
        public_auth = (method, path) in PUBLIC_AUTH_ROUTES
        start = perf_counter()
        request_id = uuid4().hex
        scope.setdefault('state', {})['request_id'] = request_id
        response_started = False
        failure_reported = False

        def route_label():
            if public_static:
                return '<static>'
            return getattr(scope.get('route'), 'path', None) or (path if path in AUTH_PATHS else '<unmatched>')

        async def send_response(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                response_started = True
                headers = MutableHeaders(scope=message)
                headers['X-Request-ID'] = request_id
                if public_static:
                    if headers.get('Content-Type', '').partition(';')[0] == 'text/html':
                        headers['Content-Security-Policy'] = PAGE_CONTENT_SECURITY_POLICY
                        headers['X-Frame-Options'] = 'DENY'
                else:
                    # 登录状态和账号数据不会写入 HTTP 缓存。
                    headers['Cache-Control'] = 'no-store'
                status = message['status']
                logger.info('请求 %s %s 返回 %d duration_ms=%.1f', log_method, route_label(), status,
                            (perf_counter() - start) * 1000)
                if status >= 500 and not failure_reported:
                    context = account_log.get()
                    log_event('request_server_error', level=ERROR, system=True, method=log_method, route=route_label(),
                              status=status, user_id=context[1].id if context is not None else None)
            await send(message)

        async def reject(status: int, detail: str, reason: str, user_id: int | None = None):
            log_event('request_rejected', level=WARNING, system=True, method=log_method, route=route_label(),
                      status=status, reason=reason, user_id=user_id, client_ip=request_client_ip(scope))
            await JSONResponse({'detail': detail}, status_code=status)(scope, receive, send_response)

        with account_log_context(None, None), request_log_context(request_id):
            try:
                await self._handle_http(request, receive, send_response, reject,
                                        public_static=public_static, public_auth=public_auth)
            except Exception as error:
                trace = error.__traceback__
                while trace.tb_next is not None:
                    trace = trace.tb_next
                location = f'{Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}'
                log_event('request_failed', level=ERROR, system=True, method=log_method, route=route_label(),
                          error=type(error).__name__, location=location)
                failure_reported = True
                if not response_started:
                    await PlainTextResponse('Internal Server Error', status_code=500)(scope, receive, send_response)
                raise

    async def _handle_http(self, request, receive, send_response, reject, *, public_static, public_auth):
        scope = request.scope
        path, method = scope['path'], scope['method']
        # 浏览器发来的跨站写请求在路由执行前拒绝，同源判断包含协议、主机和端口。
        if method not in ('GET', 'HEAD', 'OPTIONS') and (origin := request.headers.get('origin')):
            try:
                same_origin = origin_key(origin) == origin_key(str(request.base_url))
            except ValueError:
                same_origin = False
            if not same_origin:
                await reject(403, '不允许跨来源操作', 'cross_origin')
                return

        # Cookie 决定身份，账号头仅检测旧页面是否还绑定着同一个账号。
        user = self.service.get_user(request.cookies.get(COOKIE_NAME))
        requires_user = not public_static and not public_auth
        expected_user = request.headers.get('x-xiaowan-user')
        identity_changed = (requires_user or (method, path) == ('POST', '/auth/logout')) and (
            expected_user is not None and (user is None or expected_user != str(user.id)))
        if (requires_user and user is None) or identity_changed:
            await reject(401, '请先登录', 'session_account_changed' if identity_changed else 'authentication_required',
                         user.id if user is not None else None)
            return
        state = scope.setdefault('state', {})
        state['user'] = user
        state['username'] = user.username if user is not None else None
        tracked_user = user if user is not None and path not in ('/auth/login', '/auth/register') and not public_static else None
        deleting = (method, path) == ('DELETE', '/auth/account')
        user_id = tracked_user.id if tracked_user is not None else None
        if user_id is not None and (user_id in self.deleting or (deleting and self.active[user_id])):
            await reject(409, '该账号还有请求正在处理，请稍后重试', 'account_busy', user_id)
            return

        # 认证完成后绑定账号日志上下文，其余请求使用系统日志上下文。
        if user_id is not None:
            if deleting:
                self.deleting.add(user_id)
            else:
                self.active[user_id] += 1
        try:
            # 注销的请求日志写入系统日志，避免重新创建已注销账号的目录。
            if self.user_data_paths is not None and tracked_user is not None and not deleting:
                self.user_data_paths.bind(tracked_user)
            with account_log_context(self.user_data_paths, None if deleting else tracked_user):
                try:
                    await self.app(scope, receive, send_response)
                except Exception as error:
                    if user_id is not None and not deleting:
                        log_event('request_failed', level=ERROR, error=type(error).__name__)
                    raise
        finally:
            if user_id is not None:
                if deleting:
                    self.deleting.remove(user_id)
                else:
                    self.active[user_id] -= 1
                    if not self.active[user_id]:
                        del self.active[user_id]
