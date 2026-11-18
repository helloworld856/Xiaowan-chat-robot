"""Administrator model settings and legacy preferences import."""

import json
from pathlib import Path
from threading import RLock
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr

from app.accounts.preferences import Preferences
from app.config import BACKEND_ROOT
from app.llm.model_catalog import BaseURL, ModelMerchant


def validate_api_key(value: SecretStr) -> SecretStr:
    key = value.get_secret_value().strip()
    if not key or not key.isascii() or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in key):
        raise ValueError('API Key 格式无效')
    return SecretStr(key)


ApiKey = Annotated[SecretStr, Field(min_length=1, max_length=1024), AfterValidator(validate_api_key)]


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, hide_input_in_errors=True)
    model_merchant: ModelMerchant
    model_name: str = Field(min_length=1, max_length=200)


class ModelSettings(ModelSelection):
    base_url: BaseURL = ''


class AppSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: ModelSettings | None = None
    preferences: Preferences = Field(default_factory=Preferences)


class SettingsStore:
    """Read old settings for the one-time account configuration migration."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()

    def get(self) -> AppSettings | None:
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return None
            # 兼容旧配置文件，仅忽略已移除的设置，保留模型与输入快捷键。
            if isinstance(data, dict) and isinstance(data.get('preferences'), dict):
                for key in ('reply_length', 'split_reply', 'playback_mode', 'reply_interval_ms'):
                    data['preferences'].pop(key, None)
            return AppSettings.model_validate(data)

settings_store = SettingsStore(BACKEND_ROOT / 'settings.json')
