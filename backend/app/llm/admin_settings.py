"""Administrator model settings and legacy preferences import."""

import json
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.accounts.preferences import Preferences
from app.config import BACKEND_ROOT
from app.json_files import atomic_write_json


ModelMerchant = Literal['deepseek', 'tongyi']


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    model_merchant: ModelMerchant
    model_name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default='', max_length=2000)

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, value):
        if not value:
            return ''
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise ValueError('接口地址须为有效的 HTTP(S) 地址') from None
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('接口地址须为不含账号、参数或片段的 HTTP(S) 地址')
        try:
            parsed.port
        except ValueError:
            raise ValueError('接口地址端口无效')
        return value.rstrip('/')


class AppSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: ModelSettings | None = None
    preferences: Preferences = Field(default_factory=Preferences)


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()

    def get(self) -> AppSettings:
        with self.lock:
            if not self.path.exists():
                return AppSettings()
            data = json.loads(self.path.read_text(encoding='utf-8'))
            # 兼容旧配置文件，仅忽略已移除的设置，保留模型与输入快捷键。
            if isinstance(data, dict) and isinstance(data.get('preferences'), dict):
                for key in ('reply_length', 'split_reply', 'playback_mode', 'reply_interval_ms'):
                    data['preferences'].pop(key, None)
            return AppSettings.model_validate(data)

    def _save(self, settings: AppSettings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, settings.model_dump(include={'model'}))

    def update_model(self, model: ModelSettings):
        with self.lock:
            settings = AppSettings(model=model)
            self._save(settings)
            return settings

settings_store = SettingsStore(BACKEND_ROOT / 'settings.json')
