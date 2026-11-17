import pymysql
from fastapi import APIRouter, HTTPException, Request

from app.accounts.preferences import Preferences
from app.llm.admin_settings import AppSettings

router = APIRouter()


class UserSettings(AppSettings):
    model_ready: bool


def settings_for(request: Request, preferences: Preferences) -> UserSettings:
    user = request.state.user
    if user.role != 'admin':
        try:
            personal = request.app.state.personal_models.get(user.id)
        except (OSError, ValueError):
            personal = None
        return UserSettings(model=personal.public_model() if personal else None,
                            preferences=preferences, model_ready=personal is not None)
    model, ready = request.app.state.model_configuration.admin_status()
    return UserSettings(model=model, preferences=preferences, model_ready=ready)


@router.get('/settings', response_model=UserSettings)
def get_settings(request: Request):
    try:
        return settings_for(request, request.app.state.preferences.get(request.state.user.id))
    except (OSError, ValueError, pymysql.MySQLError):
        raise HTTPException(500, '读取设置失败，请稍后重试') from None


@router.post('/settings', response_model=UserSettings)
def save_settings(preferences: Preferences, request: Request):
    try:
        result = settings_for(request, preferences)
        request.app.state.preferences.update(request.state.user.id, preferences)
        return result
    except (OSError, ValueError, pymysql.MySQLError):
        raise HTTPException(500, '无法确认设置是否保存，请重新读取设置') from None
