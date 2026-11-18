"""Initialize all of the files belonging to one account."""

from app.accounts.repository import User
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

    def ensure(self, user: User, preferences: Preferences | None = None) -> None:
        user_id = user.id
        self.paths.ensure_log_directory(user_id)
        self.profiles.ensure(user_id, user.username)
        if user.role == 'user':
            self.models.ensure(user_id)
        self.preferences.ensure(user_id, preferences)
