"""Initialize all of the files belonging to one account."""

import re
from dataclasses import replace
from pathlib import Path
from shutil import rmtree

from app.account_logging import close_account_logs, logger
from app.accounts.files import require_directory, _sync_directory
from app.accounts.repository import Account, AccountDeleteUnknown, SQLAccountStore, User
from app.llm.personal_models import PersonalModelStore
from app.llm.model_catalog import ModelCatalog
from app.accounts.profile import ProfileStore
from app.accounts.preferences import FilePreferencesStore, Preferences, UserPreferencesStore
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

    def _staged_path(self, user_id: int) -> Path:
        return self.paths.root / f'.deleting-{user_id}'

    def restore_user(self, user: User) -> None:
        staged = self._staged_path(user.id)
        try:
            require_directory(self.paths.root)
            require_directory(staged)
        except FileNotFoundError:
            return
        self.paths.bind(user)
        original = self.paths.directory(user.id)
        if original.exists() or original.is_symlink():
            raise FileExistsError(original)
        staged.rename(original)
        _sync_directory(self.paths.root)

    def _purge_staged(self, staged: Path) -> None:
        try:
            require_directory(self.paths.root)
            require_directory(staged)
            rmtree(staged)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning('暂时无法移除已注销账号的目录 %s，下次启动时重试', staged.name)

    def recover_deletions(self, accounts: SQLAccountStore) -> None:
        try:
            require_directory(self.paths.root)
        except FileNotFoundError:
            return
        for staged in self.paths.root.glob('.deleting-*'):
            if match := re.fullmatch(r'\.deleting-([1-9][0-9]*)', staged.name):
                user = accounts.find_user_by_id(int(match.group(1)))
                if user is None:
                    self._purge_staged(staged)
                else:
                    self.restore_user(user)

    def delete_account(self, accounts: SQLAccountStore, account: Account) -> None:
        user = account.user
        self.paths.bind(user)
        self.restore_user(user)
        close_account_logs(self.paths, user.id)
        original = self.paths.directory(user.id)
        staged = self._staged_path(user.id)
        try:
            require_directory(self.paths.root)
            require_directory(original)
        except FileNotFoundError:
            pass
        else:
            original.rename(staged)
        try:
            if staged.exists():
                _sync_directory(self.paths.root)
            accounts.delete(account)
        except AccountDeleteUnknown:
            # 保留暂存目录。登录或启动时按数据库中是否仍有账号决定恢复或清理。
            raise
        except Exception:
            self.restore_user(user)
            raise
        self.models.forget(user.id)
        self.paths.unbind(user.id)
        self._purge_staged(staged)

    def migrate_legacy(self, accounts: SQLAccountStore) -> None:
        for user in accounts.users_with_id_directories():
            if self.paths.migrate_legacy_directory(user):
                accounts.use_username_directory(user.id)
                self.paths.bind(replace(user, uses_username_directory=True))
            else:
                logger.warning('账号 %d 的迁移目标目录已存在，继续使用数字目录', user.id)

    def ensure(self, user: User, preferences: Preferences | None = None) -> None:
        self.paths.bind(user)
        user_id = user.id
        self.paths.ensure_log_directory(user_id)
        self.profiles.ensure(user_id, user.username)
        if user.role == 'user':
            self.models.ensure(user_id)
        self.preferences.ensure(user_id, preferences)
