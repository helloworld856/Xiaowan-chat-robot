"""固定管理员的非凭据资料，与登录凭据和聊天数据库分别存储。"""
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
AvatarId = Literal['cat', 'fox', 'rabbit', 'bear', 'penguin', 'frog']


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nickname: str = Field(default='admin', min_length=1, max_length=24)
    avatar_id: AvatarId = DEFAULT_AVATAR_ID

    @field_validator('nickname', mode='before')
    @classmethod
    def trim_nickname(cls, value):
        return value.strip() if isinstance(value, str) else value

    @property
    def avatar_url(self) -> str:
        return f'/avatars/system/{self.avatar_id}.png'


class ProfileStore:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else Path(__file__).with_name('auth_config') / 'profile.json'
        self.lock = RLock()

    def get(self) -> Profile:
        with self.lock:
            if not self.path.exists():
                return Profile()
            return Profile.model_validate(json.loads(self.path.read_text(encoding='utf-8')))

    def update(self, profile: Profile) -> Profile:
        with self.lock:
            # 再验证以免调用者绕过请求模型写入任意头像路径或凭据字段。
            profile = Profile.model_validate(profile.model_dump())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                                 prefix='.profile-', suffix='.tmp', delete=False) as file:
                    temporary = Path(file.name)
                    json.dump(profile.model_dump(), file, ensure_ascii=False, indent=2)
                    file.write('\n')
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return profile
