from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from auth_config import (COOKIE_NAME, SESSION_TTL_SECONDS, MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH,
                         USERNAME_PATTERN, AccountStorageError, DuplicateUsername, InvalidCredentials,
                         InvalidPassword, InvalidSession, LoginThrottled, PasswordCommitUnknown, PasswordSaveFailed)


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


router = APIRouter(prefix='/auth', tags=['auth'], route_class=CredentialRoute)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    confirm_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    confirm_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


def _throttled(error: LoginThrottled) -> JSONResponse:
    return JSONResponse({'detail': f'尝试次数过多，请 {error.retry_after} 秒后再试'}, status_code=429,
                        headers={'Retry-After': str(error.retry_after)})


def _unavailable() -> JSONResponse:
    return JSONResponse({'detail': '暂时无法访问账户，请稍后重试'}, status_code=503)


def _clear_session_cookie(response: JSONResponse, request: Request) -> JSONResponse:
    response.delete_cookie(COOKIE_NAME, path='/', httponly=True,
                           secure=request.url.scheme == 'https', samesite='strict')
    return response


@router.post('/login')
def login(credentials: LoginRequest, request: Request):
    service = request.app.state.auth
    client = request.client.host if request.client else 'unknown'
    try:
        token, user = service.login(credentials.username, credentials.password, client,
                                    previous_token=request.cookies.get(COOKIE_NAME))
    except InvalidCredentials:
        return JSONResponse({'detail': '用户名或密码错误'}, status_code=401)
    except LoginThrottled as error:
        return _throttled(error)
    except AccountStorageError:
        return _unavailable()
    response = JSONResponse({'user': asdict(user)})
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL_SECONDS, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path='/')
    return response


@router.get('/session')
async def get_session(request: Request):
    user = request.state.user
    # 会话探测本身是公开操作；未登录是正常状态，不是资源请求失败。
    return {'user': asdict(user) if user is not None else None}


@router.post('/register', status_code=201)
def register(credentials: RegistrationRequest, request: Request):
    if credentials.password != credentials.confirm_password:
        return JSONResponse({'detail': '两次输入的密码不一致'}, status_code=400)
    service = request.app.state.auth
    client = request.client.host if request.client else 'unknown'
    try:
        user = service.register(credentials.username, credentials.password, client)
    except DuplicateUsername:
        return JSONResponse({'detail': '该用户名已注册'}, status_code=409)
    except (InvalidPassword, ValueError) as error:
        return JSONResponse({'detail': str(error)}, status_code=400)
    except LoginThrottled as error:
        return _throttled(error)
    except AccountStorageError:
        return _unavailable()
    return {'user': asdict(user)}


@router.post('/logout')
async def logout(request: Request):
    request.app.state.auth.logout(request.cookies.get(COOKIE_NAME))
    return _clear_session_cookie(JSONResponse({'ok': True}), request)


@router.post('/password')
def change_password(credentials: PasswordRequest, request: Request):
    if credentials.new_password != credentials.confirm_password:
        return JSONResponse({'detail': '两次输入的新密码不一致'}, status_code=400)
    service = request.app.state.auth
    client = request.client.host if request.client else 'unknown'
    try:
        service.change_password(request.state.user, request.cookies.get(COOKIE_NAME),
                                credentials.current_password, credentials.new_password, client)
    except InvalidSession:
        return JSONResponse({'detail': '请先登录'}, status_code=401)
    except InvalidCredentials:
        return JSONResponse({'detail': '当前密码错误'}, status_code=400)
    except InvalidPassword as error:
        return JSONResponse({'detail': str(error)}, status_code=400)
    except LoginThrottled as error:
        return _throttled(error)
    except PasswordCommitUnknown:
        return _clear_session_cookie(JSONResponse({'detail': '无法确认密码是否已更改，请重新登录'}, status_code=401), request)
    except PasswordSaveFailed:
        return JSONResponse({'detail': '保存密码失败，原有密码仍有效'}, status_code=500)
    except AccountStorageError:
        return _unavailable()
    return _clear_session_cookie(JSONResponse({'ok': True}), request)
