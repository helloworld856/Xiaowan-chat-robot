"""Store personal model credentials separately from administrator configuration."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.llm.admin_settings import ModelMerchant, ModelSettings
from app.accounts.files import UserDataPaths, protect_file, read_text, replace_json


class PersonalModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    model_merchant: ModelMerchant
    model_name: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator('api_key')
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value().strip()
        if not key or not key.isascii() or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in key):
            raise ValueError('API Key 格式无效')
        return SecretStr(key)

    def public_model(self) -> ModelSettings:
        return ModelSettings(model_merchant=self.model_merchant, model_name=self.model_name)

    def create_agents(self):
        from agent_config import create_agents

        return create_agents(self.model_merchant, self.api_key.get_secret_value(), self.model_name, '',
                             official_only=True)


class _PersonalSettings(BaseModel):
    model: PersonalModel | None


class PersonalModelStore:
    def __init__(self, root: Path | UserDataPaths):
        self.paths = root if isinstance(root, UserDataPaths) else UserDataPaths(root)
        self._agents = {}

    def path_for(self, user_id: int) -> Path:
        return self.paths.file(user_id, 'model.json')

    @staticmethod
    def _write(path: Path, model: PersonalModel | None):
        data = None
        if model is not None:
            data = model.model_dump()
            data['api_key'] = model.api_key.get_secret_value()
        replace_json(path, {'model': data})

    def ensure(self, user_id: int):
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            if not protect_file(path):
                self._write(path, None)

    @staticmethod
    def read_file(path: Path) -> PersonalModel | None:
        return _PersonalSettings.model_validate_json(read_text(path)).model

    def _read(self, user_id: int, path: Path) -> PersonalModel | None:
        try:
            return self.read_file(path)
        except FileNotFoundError:
            # Existing users get the same empty configuration on first access.
            self.paths.ensure_directory(user_id)
            self._write(path, None)
            return None

    def get(self, user_id: int) -> PersonalModel | None:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            return self._read(user_id, path)

    def update(self, user_id: int, model: PersonalModel, agents):
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            self._write(path, model)
            self._agents[user_id] = (model, agents)

    def agents_for(self, user_id: int):
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            model = self._read(user_id, path)
            if model is None:
                self._agents.pop(user_id, None)
                return None
            cached = self._agents.get(user_id)
            if cached is not None and cached[0] == model:
                return cached[1]
            agents = model.create_agents()
            self._agents[user_id] = (model, agents)
            return agents
