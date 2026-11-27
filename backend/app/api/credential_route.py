from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.account_logging import log_event


# 认证和模型配置共用此路由类，校验错误只返回字段诊断。
class CredentialRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                user = None if self.path in ('/auth/login', '/auth/register') else getattr(request.state, 'user', None)
                log_event('credential_validation_failed', system=self.path.startswith('/auth/'), route=self.path,
                          user_id=user.id if user is not None else None)
                # Validation inputs can contain the password or the entire credential object.
                details = [
                    {'type': item['type'], 'msg': item['msg'],
                     'loc': ['body'] if item['type'] == 'extra_forbidden' else item['loc']}
                    for item in error.errors()
                ]
                return JSONResponse({'detail': details}, status_code=422)

        return handle
