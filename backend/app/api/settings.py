import pymysql
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.accounts.preferences import Preferences
from app.llm.admin_settings import AppSettings
from app.llm.model_catalog import ModelMerchant

router = APIRouter()


class ProviderOptions(BaseModel):
    default_model: str
    default_address: str
    names: list[str]
    saved_model: str | None = None
    saved_address: str | None = None
    has_saved_key: bool = False


class ModelOptions(BaseModel):
    default_provider: ModelMerchant
    providers: dict[ModelMerchant, ProviderOptions]


class UserSettings(AppSettings):
    model_ready: bool
    model_options: ModelOptions


def model_options_for(request: Request) -> ModelOptions:
    user = request.state.user
    catalog = request.app.state.personal_models.catalog
    addresses = {}
    saved_models = {}
    saved_keys = set()
    try:
        if user.role == 'admin':
            admin = request.app.state.model_configuration.admin_models.get()
            addresses = {merchant: entry.base_url for merchant, entry in admin.providers.items()}
            saved_models = {merchant: entry.model_name for merchant, entry in admin.providers.items()}
            saved_keys = {merchant for merchant in catalog.providers if admin.key_for(merchant) is not None}
        else:
            choices = request.app.state.personal_models.saved_choices(user.id)
            addresses = {merchant: address for merchant, (_, address, _) in choices.items()
                         if merchant in catalog.providers and address != catalog.providers[merchant].base_url}
            saved_models = {merchant: name for merchant, (name, _, _) in choices.items()}
            saved_keys = {merchant for merchant, (_, _, has_key) in choices.items() if has_key}
    except (OSError, ValueError):
        pass
    # 只构造网页需要的公开字段，任何账号的密钥都不会随设置返回。
    return ModelOptions(default_provider=catalog.default_provider, providers={
        merchant: ProviderOptions(default_model=provider.default_model,
                                  default_address=provider.base_url,
                                  names=provider.models, saved_model=saved_models.get(merchant),
                                  saved_address=addresses.get(merchant), has_saved_key=merchant in saved_keys)
        for merchant, provider in catalog.providers.items()
    })


def settings_for(request: Request, preferences: Preferences) -> UserSettings:
    user = request.state.user
    options = model_options_for(request)
    if user.role != 'admin':
        try:
            personal = request.app.state.personal_models.get(user.id)
        except (OSError, ValueError):
            personal = None
        # 个人模型只返回公开字段，API Key 留在服务端。
        return UserSettings(model=personal.public_model() if personal else None,
                            preferences=preferences, model_ready=personal is not None, model_options=options)
    model, ready = request.app.state.model_configuration.admin_status()
    return UserSettings(model=model, preferences=preferences, model_ready=ready, model_options=options)


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
