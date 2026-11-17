"""为每个账号保存独立的个人资料。"""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.account_logging import logger
from app.accounts.files import UserDataPaths, protect_file, read_text, replace_json

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
    def __init__(self, root: Path | UserDataPaths | None = None):
        self.paths = root if isinstance(root, UserDataPaths) else UserDataPaths(root)
        self.root = self.paths.root

    def path_for(self, user_id: int) -> Path:
        return self.paths.file(user_id, 'profile.json')

    @staticmethod
    def read_file(path: Path) -> Profile:
        return Profile.model_validate_json(read_text(path))

    def ensure(self, user_id: int, username: str) -> None:
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            if not protect_file(self.path_for(user_id)):
                self.update(user_id, Profile(nickname=username))

    def get(self, user_id: int, username: str) -> Profile:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            try:
                return self.read_file(path)
            except FileNotFoundError:
                return self.update(user_id, Profile(nickname=username))
            except ValueError:
                logger.warning('个人资料文件格式无效，暂时使用默认资料')
            return Profile(nickname=username)

    def import_legacy_profile(self, user_id: int, legacy_path: Path):
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            if path.exists() or not legacy_path.exists():
                return
            self.update(user_id, self.read_file(legacy_path))

    def update(self, user_id: int, profile: Profile) -> Profile:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            # 再验证以免调用者绕过请求模型写入任意头像路径或凭据字段。
            profile = Profile.model_validate(profile.model_dump())
            self.paths.ensure_directory(user_id)
            replace_json(path, profile.model_dump())
            return profile
