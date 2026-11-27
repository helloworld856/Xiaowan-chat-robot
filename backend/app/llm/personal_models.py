"""Store personal model credentials separately from administrator configuration."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.accounts.files import UserDataPaths, protect_file, read_text, replace_json
from app.json_files import backup_provider_migration
from app.llm.admin_settings import ApiKey, ModelSelection, ModelSettings
from app.llm.model_catalog import (BaseURL, ModelAdapter, ModelCatalog, ModelEndpoint, ModelMerchant,
                                   default_model_catalog, migration_model_name)
from app.llm.public_endpoint import PublicEndpoint


class PersonalModelAddressChange(ValueError):
    pass


class PersonalModel(ModelSelection):
    api_key: ApiKey | None = None
    base_url: BaseURL | None = None


class ActivePersonalModel(ModelSettings):
    api_key: ApiKey
    adapter: ModelAdapter
    public_only: bool = False

    def public_model(self) -> ModelSettings:
        return ModelSettings(model_merchant=self.model_merchant, model_name=self.model_name,
                             base_url=self.base_url)

    def create_agents(self):
        from app.llm.factory import create_agents

        return create_agents(self.model_merchant, self.api_key.get_secret_value(), self.model_name,
                             base_url=self.base_url, adapter=self.adapter, public_only=self.public_only)


class _PersonalEntry(ModelEndpoint):
    api_key: ApiKey | None = None


class _ProviderEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    model_name: str = Field(min_length=1, max_length=200)
    base_url: BaseURL = Field(min_length=1)
    api_key: ApiKey | None = None


class _PersonalSettings(BaseModel):
    active_provider: ModelMerchant | None = None
    providers: dict[ModelMerchant, _ProviderEntry] = Field(default_factory=dict)
    # 旧账号文件里的运维配置继续使用原协议，网页填写的地址使用公网传输。
    public_overrides: set[ModelMerchant] = Field(default_factory=set)


class _GroupedSettings(BaseModel):
    active: ModelSelection | None = None
    models: dict[ModelMerchant, dict[str, _PersonalEntry]] = Field(default_factory=dict)


class _LegacySettings(BaseModel):
    model: PersonalModel | None
    base_url: str | None = None


class PersonalModelStore:
    def __init__(self, root: Path | UserDataPaths, catalog: ModelCatalog | None = None):
        self.paths = root if isinstance(root, UserDataPaths) else UserDataPaths(root)
        self.catalog = catalog or default_model_catalog()
        self._agents = {}

    def forget(self, user_id: int) -> None:
        with self.paths.lock(user_id):
            self._agents.pop(user_id, None)

    def path_for(self, user_id: int) -> Path:
        return self.paths.file(user_id, 'model.json')

    @staticmethod
    def _write(path: Path, settings: _PersonalSettings):
        try:
            previous = read_text(path)
        except FileNotFoundError:
            pass
        else:
            grouped = json.loads(previous).get('models', {})
            if any(len(entries) > 1 for entries in grouped.values()):
                backup_provider_migration(path, previous)
        data = settings.model_dump(mode='json')
        if not settings.public_overrides:
            data.pop('public_overrides')
        for merchant, entry in settings.providers.items():
            if entry.api_key is not None:
                data['providers'][merchant]['api_key'] = entry.api_key.get_secret_value()
        replace_json(path, data)

    def _defaults(self) -> _PersonalSettings:
        return _PersonalSettings(providers={
            merchant: _ProviderEntry(model_name=provider.default_model, base_url=provider.base_url)
            for merchant, provider in self.catalog.providers.items()
        })

    def _decode(self, path: Path) -> tuple[_PersonalSettings, bool]:
        data = json.loads(read_text(path))
        if isinstance(data, dict) and 'providers' in data:
            return _PersonalSettings.model_validate(data), False
        state = self._defaults()
        if isinstance(data, dict) and 'models' in data:
            grouped = _GroupedSettings.model_validate(data)
            state.active_provider = grouped.active.model_merchant if grouped.active else None
            for merchant, entries in grouped.models.items():
                if entries:
                    active = grouped.active.model_name if merchant == state.active_provider else None
                    provider = self.catalog.providers.get(merchant)
                    name = migration_model_name({name: entry.api_key is not None for name, entry in entries.items()},
                                                active, provider.default_model if provider else None)
                    state.providers[merchant] = _ProviderEntry(model_name=name, base_url=entries[name].base_url,
                                                               api_key=entries[name].api_key)
            return state, True

        legacy = _LegacySettings.model_validate(data)
        if legacy.model is not None:
            model = legacy.model
            endpoint = self.catalog.endpoint(model.model_merchant)
            state.active_provider = model.model_merchant
            state.providers[model.model_merchant] = _ProviderEntry(
                model_name=model.model_name, base_url=legacy.base_url or endpoint.base_url,
                api_key=model.api_key,
            )
        return state, True

    def _resolve(self, model: PersonalModel, settings: _PersonalSettings) -> ActivePersonalModel | None:
        merchant, name = model.model_merchant, model.model_name
        saved = settings.providers.get(merchant)
        key = model.api_key if model.api_key is not None else (saved.api_key if saved else None)
        if key is None:
            return None
        endpoint = self.catalog.endpoint(merchant)
        if model.base_url is None:
            base_url = saved.base_url if saved else endpoint.base_url
            public_only = merchant in settings.public_overrides
        else:
            base_url = model.base_url or endpoint.base_url
            public_only = base_url != endpoint.base_url
        if model.api_key is None and saved is not None and base_url != saved.base_url:
            raise PersonalModelAddressChange('更换接口地址时，请重新填写此地址的 API Key')
        if public_only:
            PublicEndpoint(base_url)
        return ActivePersonalModel(model_merchant=merchant, model_name=name, api_key=key,
                                   base_url=base_url, adapter='openai' if public_only else endpoint.adapter,
                                   public_only=public_only)

    def _active(self, settings: _PersonalSettings) -> ActivePersonalModel | None:
        merchant = settings.active_provider
        if merchant is None or merchant not in settings.providers:
            return None
        return self._resolve(PersonalModel(model_merchant=merchant, model_name=settings.providers[merchant].model_name),
                             settings)

    def ensure(self, user_id: int):
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            if not protect_file(path):
                self._write(path, self._defaults())

    def _read(self, user_id: int, *, persist: bool) -> _PersonalSettings:
        path = self.path_for(user_id)
        try:
            settings, migrated = self._decode(path)
        except FileNotFoundError:
            settings, migrated = self._defaults(), True
        if migrated and persist:
            self.paths.ensure_directory(user_id)
            self._write(path, settings)
        return settings

    def get(self, user_id: int) -> ActivePersonalModel | None:
        with self.paths.lock(user_id):
            return self._active(self._read(user_id, persist=True))

    def selection(self, user_id: int, model: PersonalModel) -> ActivePersonalModel | None:
        with self.paths.lock(user_id):
            return self._resolve(model, self._read(user_id, persist=False))

    def saved_choices(self, user_id: int) -> dict[ModelMerchant, tuple[str, str, bool]]:
        with self.paths.lock(user_id):
            return {merchant: (entry.model_name, entry.base_url, entry.api_key is not None)
                    for merchant, entry in self._read(user_id, persist=False).providers.items()}

    def update(self, user_id: int, model: PersonalModel, agents, *, checked: ActivePersonalModel | None = None) -> bool:
        with self.paths.lock(user_id):
            settings = self._read(user_id, persist=False)
            resolved = self._resolve(model, settings)
            if resolved is None or (checked is not None and resolved != checked):
                return False
            merchant, name = resolved.model_merchant, resolved.model_name
            settings.active_provider = merchant
            if resolved.public_only:
                settings.public_overrides.add(merchant)
            else:
                settings.public_overrides.discard(merchant)
            settings.providers[merchant] = _ProviderEntry(
                model_name=name, base_url=resolved.base_url, api_key=resolved.api_key,
            )
            self.paths.ensure_directory(user_id)
            self._write(self.path_for(user_id), settings)
            # 文件写入成功后再替换本账号的代理缓存。
            self._agents[user_id] = (resolved, agents)
            return True

    def agents_for(self, user_id: int):
        with self.paths.lock(user_id):
            model = self._active(self._read(user_id, persist=True))
            if model is None:
                self._agents.pop(user_id, None)
                return None
            cached = self._agents.get(user_id)
            # 配置变更时重建代理，缓存命中时复用原代理。
            if cached is not None and cached[0] == model:
                return cached[1]
            agents = model.create_agents()
            self._agents[user_id] = (model, agents)
            return agents
