"""Account input preferences and backward compatibility with the legacy table."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.account_logging import logger
from app.accounts.files import UserDataPaths, protect_file, read_text, replace_json


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    send_shortcut: Literal['enter', 'ctrl_enter'] = 'enter'


class UserPreferencesStore:
    def __init__(self, pool):
        self._pool = pool

    def find(self, user_id: int) -> Preferences | None:
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT send_shortcut FROM user_preferences WHERE user_id=%s', (user_id,))
            row = cursor.fetchone()
        return Preferences(send_shortcut=row[0]) if row else None

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


class FilePreferencesStore:
    def __init__(self, paths: UserDataPaths, legacy: UserPreferencesStore):
        self.paths = paths
        self.legacy = legacy

    def path_for(self, user_id: int) -> Path:
        return self.paths.file(user_id, 'preferences.json')

    @staticmethod
    def read_file(path: Path) -> Preferences:
        return Preferences.model_validate_json(read_text(path))

    def ensure(self, user_id: int, defaults: Preferences | None = None) -> None:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            if protect_file(path):
                return
        # Registration holds a database connection while creating files. Never query
        # the old preferences table while holding a file lock.
        initial = defaults or self.legacy.find(user_id) or Preferences()
        with self.paths.lock(user_id):
            if not protect_file(path):
                self.update(user_id, initial)

    def get(self, user_id: int) -> Preferences:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            try:
                return self.read_file(path)
            except FileNotFoundError:
                pass
            except ValueError:
                logger.warning('个人输入偏好文件格式无效，暂时使用默认设置')
                return Preferences()
        self.ensure(user_id)
        with self.paths.lock(user_id):
            return self.read_file(path)

    def update(self, user_id: int, preferences: Preferences) -> None:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            replace_json(path, preferences.model_dump())
