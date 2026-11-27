from logging import WARNING

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.account_logging import log_event
from app.accounts.portrait import ManualPortrait, UserPortrait


class PortraitUpdatePayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manual: ManualPortrait = Field(default_factory=ManualPortrait)
    auto_learn: bool = True


class PortraitResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manual: ManualPortrait = Field(default_factory=ManualPortrait)
    auto_learn: bool = True
    has_learned: bool = False


def _to_response(portrait: UserPortrait) -> PortraitResponse:
    return PortraitResponse(
        manual=portrait.manual,
        auto_learn=portrait.auto_learn,
        has_learned=bool(portrait.learned),
    )


router = APIRouter()


@router.get('/portrait', response_model=PortraitResponse)
def get_portrait(request: Request):
    try:
        current = request.app.state.portraits.get(request.state.user.id)
        return _to_response(current)
    except (OSError, ValueError) as error:
        log_event('portrait_read_failed', level=WARNING, error=type(error).__name__)
        raise HTTPException(500, '读取用户画像失败，请稍后重试') from None


@router.post('/portrait', response_model=PortraitResponse)
def save_portrait(payload: PortraitUpdatePayload, request: Request):
    try:
        saved = request.app.state.portraits.update_manual(
            request.state.user.id, payload.manual, auto_learn=payload.auto_learn,
        )
        return _to_response(saved)
    except (OSError, ValueError) as error:
        log_event('portrait_save_failed', level=WARNING, error=type(error).__name__)
        raise HTTPException(500, '保存用户画像失败，请稍后重试') from None


@router.delete('/portrait/learned')
def clear_learned_portrait(request: Request):
    try:
        saved = request.app.state.portraits.clear_learned(request.state.user.id)
        return {'ok': True, 'has_learned': bool(saved.learned)}
    except (OSError, ValueError) as error:
        log_event('portrait_clear_learned_failed', level=WARNING, error=type(error).__name__)
        raise HTTPException(500, '清空印象失败，请稍后重试') from None
