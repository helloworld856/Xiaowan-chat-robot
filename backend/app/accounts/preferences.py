"""账号文件中的偏好，以及旧版快捷键表的迁移兼容。"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.account_logging import logger
from app.accounts.files import UserDataPaths, protect_file, read_text, replace_json


ThemeChoice = Literal['theme0', 'theme1', 'theme2', 'theme3', 'theme4']


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    send_shortcut: Literal['enter', 'ctrl_enter'] = 'enter'
    theme: ThemeChoice = 'theme0'
    chat_font_size: Literal['small', 'standard', 'large'] = 'standard'


# 旧版 MySQL 表供缺少偏好文件的账号导入快捷键。
class UserPreferencesStore:
    def __init__(self, pool):
        self._pool = pool

    def find(self, user_id: int) -> Preferences | None:
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT send_shortcut FROM user_preferences WHERE user_id=%s', (user_id,))
            row = cursor.fetchone()
        return Preferences(send_shortcut=row[0]) if row else None

    def import_legacy(self, user_id: int, preferences: Preferences):
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute('''INSERT INTO user_preferences (user_id, send_shortcut) VALUES (%s, %s)
                              ON DUPLICATE KEY UPDATE user_id=%s''',
                           (user_id, preferences.send_shortcut, user_id))
            connection.commit()


class FilePreferencesStore:
    def __init__(self, paths: UserDataPaths, legacy: UserPreferencesStore, defaults: Preferences | None = None):
        self.paths = paths
        self.legacy = legacy
        self.defaults = defaults or Preferences()

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
        # 注册时会先占用数据库连接再创建文件。这里先释放文件锁，再查询旧库，避免互相等待。
        initial = defaults or self.legacy.find(user_id) or self.defaults
        with self.paths.lock(user_id):
            # 重新拿到锁后检查文件，优先保留查询期间已经保存的设置。
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
                logger.warning('个人偏好文件格式无效，暂时使用默认设置')
                return self.defaults.model_copy()
        self.ensure(user_id)
        with self.paths.lock(user_id):
            return self.read_file(path)

    def update(self, user_id: int, preferences: Preferences) -> None:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            replace_json(path, preferences.model_dump())
