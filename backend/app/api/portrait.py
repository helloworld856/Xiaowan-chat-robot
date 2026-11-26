from logging import WARNING

from fastapi import APIRouter, HTTPException, Request

from app.account_logging import log_event
from app.accounts.portrait import UserPortrait

router = APIRouter()


@router.get('/portrait', response_model=UserPortrait)
def get_portrait(request: Request):
    try:
        return request.app.state.portraits.get(request.state.user.id)
    except (OSError, ValueError) as error:
        log_event('portrait_read_failed', level=WARNING, error=type(error).__name__)
        raise HTTPException(500, '读取用户画像失败，请稍后重试') from None


@router.post('/portrait', response_model=UserPortrait)
def save_portrait(portrait: UserPortrait, request: Request):
    try:
        return request.app.state.portraits.update(request.state.user.id, portrait)
    except (OSError, ValueError) as error:
        log_event('portrait_save_failed', level=WARNING, error=type(error).__name__)
        raise HTTPException(500, '保存用户画像失败，请稍后重试') from None
