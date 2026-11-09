"""单人本地模式的非密钥设置。账号接入后可替换存储层。"""
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    model_merchant: Literal['deepseek', 'tongyi']
    model_name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default='', max_length=2000)

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, value):
        if not value:
            return ''
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('接口地址须为不含账号、参数或片段的 HTTP(S) 地址')
        try:
            parsed.port
        except ValueError:
            raise ValueError('接口地址端口无效')
        return value.rstrip('/')


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    send_shortcut: Literal['enter', 'ctrl_enter'] = 'enter'


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
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.settings-', suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                json.dump(settings.model_dump(), file, ensure_ascii=False, indent=2)
                file.write('\n')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def update_model(self, model: ModelSettings):
        with self.lock:
            settings = self.get()
            settings.model = model
            self._save(settings)
            return settings

    def update_preferences(self, preferences: Preferences):
        with self.lock:
            settings = self.get()
            settings.preferences = preferences
            self._save(settings)
            return settings


settings_store = SettingsStore(Path(__file__).resolve().parent / 'settings.json')
