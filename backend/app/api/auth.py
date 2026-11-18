from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.accounts.auth_service import (
    COOKIE_NAME,
    USERNAME_PATTERN,
    InvalidCredentials,
    InvalidPassword,
    InvalidSession,
    LoginThrottled,
    PasswordSaveFailed,
)
from app.accounts.repository import (
    AccountStorageError,
    DuplicateUsername,
    PasswordCommitUnknown,
    ReservedUsername,
)
from app.system_config import MAX_PASSWORD_INPUT_LENGTH, PasswordConfig

from .credential_route import CredentialRoute


router = APIRouter(prefix='/auth', tags=['auth'], route_class=CredentialRoute)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_INPUT_LENGTH)


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_INPUT_LENGTH)
    new_password: str = Field(max_length=MAX_PASSWORD_INPUT_LENGTH)
    confirm_password: str = Field(max_length=MAX_PASSWORD_INPUT_LENGTH)


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(max_length=MAX_PASSWORD_INPUT_LENGTH)
    confirm_password: str = Field(max_length=MAX_PASSWORD_INPUT_LENGTH)


def _validate_password_lengths(policy: PasswordConfig, **passwords: str) -> None:
    errors = []
    for field, password in passwords.items():
        if len(password) < policy.min_length:
            error_type = 'string_too_short'
            message = f'String should have at least {policy.min_length} characters'
        elif len(password) > policy.max_length:
            error_type = 'string_too_long'
            message = f'String should have at most {policy.max_length} characters'
        else:
            continue
        errors.append({'type': error_type, 'loc': ['body', field], 'msg': message})
    if errors:
        raise RequestValidationError(errors)


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
    response.set_cookie(COOKIE_NAME, token, max_age=service.config.session_ttl_seconds, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path='/')
    return response


@router.get('/session')
async def get_session(request: Request):
    user = request.state.user
    password = request.app.state.auth.config.password
    config = request.app.state.system_config
    # 会话探测本身是公开操作；未登录是正常状态，不是资源请求失败。
    return {
        'user': asdict(user) if user is not None else None,
        'limits': {
            'password_min_length': password.min_length,
            'password_max_length': password.max_length,
            'max_nickname_chars': config.profile.max_nickname_chars,
            'max_message_chars': config.chat.max_message_chars,
        },
    }


@router.post('/register', status_code=201)
def register(credentials: RegistrationRequest, request: Request):
    service = request.app.state.auth
    _validate_password_lengths(service.config.password, password=credentials.password,
                               confirm_password=credentials.confirm_password)
    if credentials.password != credentials.confirm_password:
        return JSONResponse({'detail': '两次输入的密码不一致'}, status_code=400)
    client = request.client.host if request.client else 'unknown'
    try:
        user = service.register(credentials.username, credentials.password, client)
    except ReservedUsername:
        return JSONResponse({'detail': 'admin 是保留用户名'}, status_code=409)
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
    service = request.app.state.auth
    _validate_password_lengths(service.config.password, new_password=credentials.new_password,
                               confirm_password=credentials.confirm_password)
    if credentials.new_password != credentials.confirm_password:
        return JSONResponse({'detail': '两次输入的新密码不一致'}, status_code=400)
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
        # 提交结果未知时清除 Cookie，让用户重新登录确认当前密码。
        return _clear_session_cookie(JSONResponse({'detail': '无法确认密码是否已更改，请重新登录'}, status_code=401), request)
    except PasswordSaveFailed:
        return JSONResponse({'detail': '保存密码失败，原有密码仍有效'}, status_code=500)
    except AccountStorageError:
        return _unavailable()
    return _clear_session_cookie(JSONResponse({'ok': True}), request)
