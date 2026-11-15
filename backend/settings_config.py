"""全站模型设置及用户输入习惯的存储。"""
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


class UserSettings(AppSettings):
    model_ready: bool


class UserPreferencesStore:
    def __init__(self, pool):
        self._pool = pool

    def get(self, user_id: int) -> Preferences:
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT send_shortcut FROM user_preferences WHERE user_id=%s', (user_id,))
            row = cursor.fetchone()
        return Preferences(send_shortcut=row[0]) if row else Preferences()

    def update(self, user_id: int, preferences: Preferences):
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute('''INSERT INTO user_preferences (user_id, send_shortcut) VALUES (%s, %s)
                              ON DUPLICATE KEY UPDATE send_shortcut=%s''',
                           (user_id, preferences.send_shortcut, preferences.send_shortcut))
            connection.commit()

    def import_legacy(self, user_id: int, preferences: Preferences):
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute('''INSERT INTO user_preferences (user_id, send_shortcut) VALUES (%s, %s)
                              ON DUPLICATE KEY UPDATE user_id=%s''',
                           (user_id, preferences.send_shortcut, user_id))
            connection.commit()


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
                json.dump(settings.model_dump(include={'model'}), file, ensure_ascii=False, indent=2)
                file.write('\n')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def update_model(self, model: ModelSettings):
        with self.lock:
            settings = AppSettings(model=model)
            self._save(settings)
            return settings

settings_store = SettingsStore(Path(__file__).resolve().parent / 'settings.json')
