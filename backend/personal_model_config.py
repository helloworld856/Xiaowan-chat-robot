"""Store personal model credentials separately from administrator configuration."""

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from settings_config import ModelMerchant, ModelSettings


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

        return create_agents(self.model_merchant, self.api_key.get_secret_value(), self.model_name, '')


class _PersonalSettings(BaseModel):
    model: PersonalModel | None


class PersonalModelStore:
    def __init__(self, root: Path):
        self.root = root
        self._locks = tuple(Lock() for _ in range(64))
        self._agents = {}

    def _path(self, user_id: int) -> Path:
        if type(user_id) is not int or user_id < 1:
            raise ValueError('账号 ID 无效')
        return self.root / str(user_id) / 'model.json'

    @staticmethod
    def _write(path: Path, model: PersonalModel | None):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = None
        if model is not None:
            data = model.model_dump()
            data['api_key'] = model.api_key.get_secret_value()
        temporary = None
        try:
            # NamedTemporaryFile creates the file with permissions limited to its owner on POSIX.
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.model-', suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                json.dump({'model': data}, file, ensure_ascii=False, indent=2)
                file.write('\n')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def ensure(self, user_id: int):
        path = self._path(user_id)
        with self._locks[user_id % len(self._locks)]:
            if not path.exists():
                self._write(path, None)

    def _read(self, path: Path) -> PersonalModel | None:
        try:
            payload = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            # Existing users get the same empty configuration on first access.
            self._write(path, None)
            return None
        return _PersonalSettings.model_validate_json(payload).model

    def get(self, user_id: int) -> PersonalModel | None:
        path = self._path(user_id)
        with self._locks[user_id % len(self._locks)]:
            return self._read(path)

    def update(self, user_id: int, model: PersonalModel, agents):
        path = self._path(user_id)
        with self._locks[user_id % len(self._locks)]:
            self._write(path, model)
            self._agents[user_id] = (model, agents)

    def agents_for(self, user_id: int):
        path = self._path(user_id)
        with self._locks[user_id % len(self._locks)]:
            model = self._read(path)
            if model is None:
                self._agents.pop(user_id, None)
                return None
            cached = self._agents.get(user_id)
            if cached is not None and cached[0] == model:
                return cached[1]
            agents = model.create_agents()
            self._agents[user_id] = (model, agents)
            return agents
