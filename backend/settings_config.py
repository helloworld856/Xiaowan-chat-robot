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
    reply_length: Literal['short', 'balanced', 'detailed'] = 'balanced'
    split_reply: bool = True
    playback_mode: Literal['sequential', 'instant'] = 'sequential'
    reply_interval_ms: int = Field(default=1000, ge=0, le=5000, strict=True)
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
            return AppSettings.model_validate_json(self.path.read_text(encoding='utf-8'))

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


def reply_guidance(preferences: Preferences) -> str:
    length = {
        'short': '优先简短回答，直接回应重点；必要信息仍需完整。',
        'balanced': '回复长度适中，按内容需要展开。',
        'detailed': '用户需要解释时充分展开，提供细节和例子，避免无关铺陈。',
    }[preferences.reply_length]
    split = ('通常回复一条消息；有自然停顿时可以分成两条，不为凑条数拆句。'
             if preferences.split_reply else '本轮只回复一条完整消息，将所有内容放在 reply 的一个元素中。')
    return f'【用户的回复偏好】{length}\n{split}'


def format_replies(replies: list[str], preferences: Preferences) -> list[str]:
    return replies if preferences.split_reply else ['\n\n'.join(replies)]


settings_store = SettingsStore(Path(__file__).resolve().parent / 'settings.json')
