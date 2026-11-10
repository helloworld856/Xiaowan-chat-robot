from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from auth_config import (COOKIE_NAME, SESSION_TTL_SECONDS, InvalidCredentials, InvalidPassword,
                         LoginThrottled, PasswordSaveFailed)

router = APIRouter(prefix='/auth', tags=['auth'])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=6, max_length=128)
    confirm_password: str = Field(min_length=6, max_length=128)


@router.post('/login')
async def login(credentials: LoginRequest, request: Request):
    service = request.app.state.auth
    client = request.client.host if request.client else 'unknown'
    try:
        token = service.login(credentials.username.strip(), credentials.password, client)
    except InvalidCredentials:
        return JSONResponse({'detail': '用户名或密码错误'}, status_code=401)
    except LoginThrottled as error:
        return JSONResponse({'detail': f'尝试次数过多，请 {error.retry_after} 秒后再试'}, status_code=429,
                            headers={'Retry-After': str(error.retry_after)})
    service.logout(request.cookies.get(COOKIE_NAME))
    response = JSONResponse({'user': {'username': 'admin'}})
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL_SECONDS, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path='/')
    return response


@router.get('/session')
async def get_session(request: Request):
    username = request.app.state.auth.get_user(request.cookies.get(COOKIE_NAME))
    # 会话探测本身是公开操作；未登录是正常状态，不是资源请求失败。
    return {'user': {'username': username} if username is not None else None}


@router.post('/logout')
async def logout(request: Request):
    request.app.state.auth.logout(request.cookies.get(COOKIE_NAME))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE_NAME, path='/', httponly=True,
                           secure=request.url.scheme == 'https', samesite='strict')
    return response


@router.post('/password')
async def change_password(credentials: PasswordRequest, request: Request):
    if credentials.new_password != credentials.confirm_password:
        return JSONResponse({'detail': '两次输入的新密码不一致'}, status_code=400)
    service = request.app.state.auth
    client = request.client.host if request.client else 'unknown'
    try:
        service.change_password(credentials.current_password, credentials.new_password, client)
    except InvalidCredentials:
        return JSONResponse({'detail': '当前密码错误'}, status_code=400)
    except InvalidPassword as error:
        return JSONResponse({'detail': str(error)}, status_code=400)
    except LoginThrottled as error:
        return JSONResponse({'detail': f'尝试次数过多，请 {error.retry_after} 秒后再试'}, status_code=429,
                            headers={'Retry-After': str(error.retry_after)})
    except PasswordSaveFailed:
        return JSONResponse({'detail': '保存密码失败，原有密码仍有效'}, status_code=500)
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE_NAME, path='/', httponly=True,
                           secure=request.url.scheme == 'https', samesite='strict')
    return response
