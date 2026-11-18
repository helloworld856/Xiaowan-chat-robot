"""为每个账号保存独立的个人资料。"""
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.account_logging import logger
from app.accounts.files import UserDataPaths, protect_file, read_text, replace_json
from app.system_config import AvatarId, DefaultsConfig, ProfileConfig

AVATARS = (
    ('cat', '小猫'), ('fox', '狐狸'), ('rabbit', '兔子'),
    ('bear', '小熊'), ('penguin', '企鹅'), ('frog', '青蛙'),
)
class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # 已保存的昵称可以超过当前上限，新提交的昵称按部署配置验证。
    nickname: str = Field(default='admin', min_length=1, max_length=256)
    avatar_id: AvatarId = 'cat'

    @field_validator('nickname', mode='before')
    @classmethod
    def trim_nickname(cls, value):
        return value.strip() if isinstance(value, str) else value

    @property
    def avatar_url(self) -> str:
        return f'/avatars/system/{self.avatar_id}.png'


class ProfileStore:
    def __init__(self, root: Path | UserDataPaths | None = None, *, config: ProfileConfig | None = None,
                 defaults: DefaultsConfig | None = None):
        self.paths = root if isinstance(root, UserDataPaths) else UserDataPaths(root)
        self.root = self.paths.root
        self.config = config or ProfileConfig()
        self.default_avatar_id = (defaults or DefaultsConfig()).avatar_id

    def _initial_profile(self, username: str) -> Profile:
        return Profile(nickname=username[:self.config.max_nickname_chars], avatar_id=self.default_avatar_id)

    def path_for(self, user_id: int) -> Path:
        return self.paths.file(user_id, 'profile.json')

    @staticmethod
    def read_file(path: Path) -> Profile:
        return Profile.model_validate_json(read_text(path))

    def ensure(self, user_id: int, username: str) -> None:
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            if not protect_file(self.path_for(user_id)):
                self.update(user_id, self._initial_profile(username))

    def get(self, user_id: int, username: str) -> Profile:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            try:
                return self.read_file(path)
            except FileNotFoundError:
                return self.update(user_id, self._initial_profile(username))
            except ValueError:
                logger.warning('个人资料文件格式无效，暂时使用默认资料')
            return self._initial_profile(username)

    # 旧资料只用于首次补齐，已有的个人资料优先。
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
