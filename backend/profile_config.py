"""为每个账号保存独立的个人资料。"""
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AVATARS = (
    ('cat', '小猫'), ('fox', '狐狸'), ('rabbit', '兔子'),
    ('bear', '小熊'), ('penguin', '企鹅'), ('frog', '青蛙'),
)
DEFAULT_AVATAR_ID = 'cat'
MAX_NICKNAME_LENGTH = 32
AvatarId = Literal['cat', 'fox', 'rabbit', 'bear', 'penguin', 'frog']


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nickname: str = Field(default='admin', min_length=1, max_length=MAX_NICKNAME_LENGTH)
    avatar_id: AvatarId = DEFAULT_AVATAR_ID

    @field_validator('nickname', mode='before')
    @classmethod
    def trim_nickname(cls, value):
        return value.strip() if isinstance(value, str) else value

    @property
    def avatar_url(self) -> str:
        return f'/avatars/system/{self.avatar_id}.png'


class ProfileStore:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else Path(__file__).with_name('user_data')
        self._locks = tuple(RLock() for _ in range(64))

    def path_for(self, user_id: int) -> Path:
        if type(user_id) is not int or user_id < 1:
            raise ValueError('账号 ID 无效')
        return self.root / str(user_id) / 'profile.json'

    @staticmethod
    def _read(path: Path) -> Profile:
        return Profile.model_validate_json(path.read_text(encoding='utf-8'))

    def get(self, user_id: int, username: str) -> Profile:
        path = self.path_for(user_id)
        with self._locks[user_id % len(self._locks)]:
            try:
                return self._read(path)
            except FileNotFoundError:
                return Profile(nickname=username)

    def import_legacy_profile(self, user_id: int, legacy_path: Path):
        path = self.path_for(user_id)
        with self._locks[user_id % len(self._locks)]:
            if path.exists() or not legacy_path.exists():
                return
            self.update(user_id, self._read(legacy_path))

    def update(self, user_id: int, profile: Profile) -> Profile:
        path = self.path_for(user_id)
        with self._locks[user_id % len(self._locks)]:
            # 再验证以免调用者绕过请求模型写入任意头像路径或凭据字段。
            profile = Profile.model_validate(profile.model_dump())
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                                 prefix='.profile-', suffix='.tmp', delete=False) as file:
                    temporary = Path(file.name)
                    json.dump(profile.model_dump(), file, ensure_ascii=False, indent=2)
                    file.write('\n')
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return profile
