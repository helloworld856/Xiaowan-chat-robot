from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.accounts.auth_service import AuthService, COOKIE_NAME
from app.account_logging import logger
from app.account_logging import account_log_context
from app.accounts.files import UserDataPaths


PAGE_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def origin_key(value: str):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid origin')
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


class AuthenticationMiddleware:
    def __init__(self, app, service: AuthService, user_data_paths: UserDataPaths | None = None):
        self.app = app
        self.service = service
        self.user_data_paths = user_data_paths

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path, method = scope['path'], scope['method']
        # 浏览器发来的跨站写请求在路由执行前拒绝，同源判断包含协议、主机和端口。
        if method not in ('GET', 'HEAD', 'OPTIONS') and (origin := request.headers.get('origin')):
            try:
                same_origin = origin_key(origin) == origin_key(str(request.base_url))
            except ValueError:
                same_origin = False
            if not same_origin:
                await JSONResponse({'detail': '不允许跨来源操作'}, status_code=403)(scope, receive, send)
                return

        public_static = method in ('GET', 'HEAD') and (
            path in ('/', '/index.html', '/favicon.ico') or path.startswith(('/assets/', '/ico/', '/avatars/')))
        public_auth = (method, path) in {
            ('POST', '/auth/login'), ('POST', '/auth/logout'), ('POST', '/auth/register'), ('GET', '/auth/session'),
        }
        # Cookie 决定身份，账号头仅检测旧页面是否还绑定着同一个账号。
        user = self.service.get_user(request.cookies.get(COOKIE_NAME))
        requires_user = not public_static and not public_auth
        expected_user = request.headers.get('x-xiaowan-user')
        identity_changed = (requires_user or (method, path) == ('POST', '/auth/logout')) and (
            expected_user is not None and (user is None or expected_user != str(user.id)))
        if (requires_user and user is None) or identity_changed:
            await JSONResponse({'detail': '请先登录'}, status_code=401,
                               headers={'Cache-Control': 'no-store'})(scope, receive, send)
            return
        state = scope.setdefault('state', {})
        state['user'] = user
        state['username'] = user.username if user is not None else None
        tracked_user = user if user is not None and path not in ('/auth/login', '/auth/register') and not public_static else None

        async def send_response(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                if public_static:
                    if headers.get('Content-Type', '').partition(';')[0] == 'text/html':
                        headers['Content-Security-Policy'] = PAGE_CONTENT_SECURITY_POLICY
                        headers['X-Frame-Options'] = 'DENY'
                else:
                    # 登录状态和账号数据不会写入 HTTP 缓存。
                    headers['Cache-Control'] = 'no-store'
                    # 只记录路由模板和状态码，避免记录 URL 参数或请求正文。
                    route = getattr(scope.get('route'), 'path', None)
                    if self.user_data_paths is not None and route is not None:
                        logger.info('请求 %s %s 返回 %d', method, route, message['status'])
            await send(message)

        # 认证完成后绑定账号日志上下文，其余请求使用系统日志上下文。
        if self.user_data_paths is not None and tracked_user is not None:
            self.user_data_paths.bind(tracked_user)
        with account_log_context(self.user_data_paths, tracked_user.id if tracked_user else None):
            try:
                await self.app(scope, receive, send_response)
            except Exception as error:
                logger.warning('请求处理异常（%s）', type(error).__name__)
                raise
