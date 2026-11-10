from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth_config import AuthService, COOKIE_NAME


def origin_key(value: str):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid origin')
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


class AuthenticationMiddleware:
    def __init__(self, app, service: AuthService):
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path, method = scope['path'], scope['method']
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
        public_auth = (method, path) in {('POST', '/auth/login'), ('POST', '/auth/logout'), ('GET', '/auth/session')}
        user = self.service.get_user(request.cookies.get(COOKIE_NAME))
        if not public_static and not public_auth and user is None:
            await JSONResponse({'detail': '请先登录'}, status_code=401,
                               headers={'Cache-Control': 'no-store'})(scope, receive, send)
            return
        scope.setdefault('state', {})['username'] = user

        async def send_private(message):
            if message['type'] == 'http.response.start' and not public_static:
                MutableHeaders(scope=message)['Cache-Control'] = 'no-store'
            await send(message)
        await self.app(scope, receive, send_private)
