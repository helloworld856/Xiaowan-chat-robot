"""Initialize all of the files belonging to one account."""

from auth_config import User
from personal_model_config import PersonalModelStore
from profile_config import ProfileStore
from settings_config import FilePreferencesStore, Preferences, UserPreferencesStore


class UserDirectoryStore:
    def __init__(self, profiles: ProfileStore, legacy_preferences: UserPreferencesStore):
        self.profiles = profiles
        self.paths = profiles.paths
        self.models = PersonalModelStore(self.paths)
        self.preferences = FilePreferencesStore(self.paths, legacy_preferences)

    def prepare_new(self, user: User) -> None:
        # This runs inside the registration transaction. The new user has no legacy settings.
        self.ensure(user, Preferences())

    def ensure(self, user: User, preferences: Preferences | None = None) -> None:
        self.paths.ensure_log_directory(user.id)
        self.profiles.ensure(user.id, user.username)
        if user.role == 'user':
            self.models.ensure(user.id)
        self.preferences.ensure(user.id, preferences)
