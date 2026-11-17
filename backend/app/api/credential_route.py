from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class CredentialRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                # Validation inputs can contain the password or the entire credential object.
                details = [
                    {'type': item['type'], 'msg': item['msg'],
                     'loc': ['body'] if item['type'] == 'extra_forbidden' else item['loc']}
                    for item in error.errors()
                ]
                return JSONResponse({'detail': details}, status_code=422)

        return handle
