"""Initialize all of the files belonging to one account."""

from functools import partial
from pathlib import Path

from app.accounts.repository import User
from app.llm.personal_models import PersonalModelStore
from app.llm.model_catalog import ModelCatalog
from app.accounts.profile import ProfileStore
from app.accounts.preferences import FilePreferencesStore, Preferences, UserPreferencesStore
from app.accounts.files import require_directory
from app.system_config import DefaultsConfig


class UserDirectoryStore:
    def __init__(self, profiles: ProfileStore, legacy_preferences: UserPreferencesStore,
                 catalog: ModelCatalog | None = None, *, defaults: DefaultsConfig | None = None):
        self.profiles = profiles
        self.paths = profiles.paths
        self.models = PersonalModelStore(self.paths, catalog)
        initial = defaults or DefaultsConfig()
        self.preferences = FilePreferencesStore(
            self.paths, legacy_preferences, Preferences(send_shortcut=initial.send_shortcut, theme=initial.theme),
        )

    def prepare_new(self, user: User) -> None:
        # This runs inside the registration transaction. The new user has no legacy settings.
        self.ensure(user, self.preferences.defaults)

    def ensure(self, user: User, preferences: Preferences | None = None) -> None:
        for _, _, ensure in self._entries(user, preferences):
            ensure()

    # 预检只读，文件格式或权限异常交给迁移报告处理。
    def inspect_missing(self, user: User) -> set[Path]:
        missing = set()
        for path, check, _ in self._entries(user):
            try:
                check(path)
            except FileNotFoundError:
                missing.add(path)
        return missing

    # 把检查和创建动作放在同一份清单里，让迁移与注册使用相同规则。
    def _entries(self, user: User, preferences: Preferences | None = None):
        user_id = user.id
        yield (self.paths.log_directory(user_id), require_directory,
               partial(self.paths.ensure_log_directory, user_id))
        yield (self.profiles.path_for(user_id), self.profiles.read_file,
               partial(self.profiles.ensure, user_id, user.username))
        if user.role == 'user':
            yield (self.models.path_for(user_id), self.models.read_file, partial(self.models.ensure, user_id))
        yield (self.preferences.path_for(user_id), self.preferences.read_file,
               partial(self.preferences.ensure, user_id, preferences))
