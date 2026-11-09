from fastapi import APIRouter, HTTPException
from settings_config import AppSettings, Preferences, settings_store

router = APIRouter()


@router.get('/settings', response_model=AppSettings)
def get_settings():
    try:
        return settings_store.get()
    except (OSError, ValueError):
        raise HTTPException(500, '读取设置失败，请检查 settings.json')


@router.post('/settings', response_model=AppSettings)
def save_settings(preferences: Preferences):
    try:
        return settings_store.update_preferences(preferences)
    except (OSError, ValueError):
        raise HTTPException(500, '保存设置失败，原有设置仍保留')
