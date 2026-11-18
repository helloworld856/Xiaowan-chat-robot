"""Store the administrator's model and provider keys in the backend .env."""

from collections.abc import Callable, Mapping
from io import StringIO
import json
from pathlib import Path
from threading import RLock

from dotenv.parser import parse_stream
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, ValidationError

from app.accounts.files import protect_file, read_text
from app.json_files import atomic_write_text, backup_provider_migration, dotenv_assignment
from app.llm.admin_settings import ApiKey, ModelSettings
from app.llm.model_catalog import BaseURL, ModelCatalog, ModelMerchant, default_model_catalog, migration_model_name


_PROVIDER_KEYS = {'deepseek': 'DEEPSEEK_API_KEY', 'tongyi': 'TONGYI_API_KEY'}
_MERCHANT = 'DEFAULT_MODEL_MERCHANT'
_MODEL_NAME = 'DEFAULT_MODEL_NAME'
_BASE_URL = 'DEFAULT_MODEL_BASE_URL'
_MODELS = 'ADMIN_MODELS'
_PROVIDERS = 'ADMIN_PROVIDERS'
_KEY_FORMAT = TypeAdapter(ApiKey)


class AdminModelEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    base_url: BaseURL = ''
    api_key: ApiKey | None = None


_MODELS_FORMAT = TypeAdapter(dict[ModelMerchant, dict[str, AdminModelEntry]])


class AdminProviderEntry(AdminModelEntry):
    model_name: str = Field(min_length=1, max_length=200)


_PROVIDERS_FORMAT = TypeAdapter(dict[ModelMerchant, AdminProviderEntry])


class AdminModelFile(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    model: ModelSettings | None = None
    api_keys: dict[ModelMerchant, ApiKey] = Field(default_factory=dict)
    providers: dict[ModelMerchant, AdminProviderEntry] = Field(default_factory=dict)

    def key_for(self, merchant: ModelMerchant) -> SecretStr | None:
        entry = self.providers.get(merchant)
        return entry.api_key if entry is not None and entry.api_key is not None else self.api_keys.get(merchant)


def _provider_keys(environment: Mapping) -> dict[ModelMerchant, SecretStr]:
    keys = {}
    for merchant, name in _PROVIDER_KEYS.items():
        raw = environment.get(name)
        if raw:
            try:
                keys[merchant] = _KEY_FORMAT.validate_python(raw)
            except ValidationError:
                # 单个密钥无效时，仍允许使用其他厂商的密钥。
                continue
    return keys


def import_legacy_admin_model(model: ModelSettings | None, environment: Mapping, *,
                              previous: Path | None = None) -> AdminModelFile:
    keys = _provider_keys(environment)
    if previous is not None:
        try:
            saved = AdminModelFile.model_validate_json(read_text(previous))
        except FileNotFoundError:
            pass
        else:
            return AdminModelFile(model=saved.model, api_keys={**keys, **saved.api_keys})
    return AdminModelFile(model=model, api_keys=keys)


class AdminModelStore:
    def __init__(self, path: Path, catalog: ModelCatalog | None = None):
        self.path = path
        self.catalog = catalog or default_model_catalog()
        self.lock = RLock()

    def _read(self):
        if not protect_file(self.path):
            return [], {}
        bindings = list(parse_stream(StringIO(read_text(self.path))))
        if any(binding.error for binding in bindings):
            raise ValueError('管理员 .env 格式无效')
        return bindings, {binding.key: binding.value for binding in bindings if binding.key is not None}

    def _state(self, values: Mapping) -> AdminModelFile:
        merchant = values.get(_MERCHANT)
        providers = self._entries(values)
        entry = providers.get(merchant)
        model = ModelSettings(model_merchant=merchant, model_name=entry.model_name,
                              base_url=entry.base_url) if entry else None
        return AdminModelFile(model=model, api_keys=_provider_keys(values), providers=providers)

    @staticmethod
    def _entries(values: Mapping) -> dict[ModelMerchant, AdminProviderEntry]:
        if _PROVIDERS in values:
            return _PROVIDERS_FORMAT.validate_json(values.get(_PROVIDERS) or '{}')
        merchant = values.get(_MERCHANT)
        active = (values.get(_MODEL_NAME) or '').strip()
        grouped = _MODELS_FORMAT.validate_json(values[_MODELS]) if values.get(_MODELS) else {}
        providers = {}
        for provider, entries in grouped.items():
            if entries:
                name = migration_model_name({name: entry.api_key is not None for name, entry in entries.items()},
                                            active if provider == merchant else None)
                providers[provider] = AdminProviderEntry(model_name=name, **entries[name].model_dump())
        if active and merchant not in providers:
            model = ModelSettings(model_merchant=merchant, model_name=active, base_url=values.get(_BASE_URL) or '')
            providers[model.model_merchant] = AdminProviderEntry(model_name=model.model_name, base_url=model.base_url)
        return providers

    def get(self) -> AdminModelFile:
        with self.lock:
            _, values = self._read()
            return self._state(values)

    def initialize(self, legacy: Callable[[], AdminModelFile]) -> AdminModelFile:
        with self.lock:
            bindings, values = self._read()
            if _PROVIDERS in values:
                return self._state(values)
            if _MODELS in values or (_MODEL_NAME in values and _BASE_URL in values):
                settings = self._state(values)
            else:
                # 更早的 .env 可能留有已停用的模型字段，优先导入当时实际使用的设置。
                settings = legacy()
                if settings.model is None and values.get(_MERCHANT) and values.get(_MODEL_NAME):
                    settings.model = self._state(values).model
            if settings.model is not None:
                model = settings.model
                if model.model_merchant not in settings.providers:
                    settings.providers[model.model_merchant] = AdminProviderEntry(
                        model_name=model.model_name, base_url=model.base_url,
                        api_key=settings.api_keys.get(model.model_merchant),
                    )
            for merchant, key in settings.api_keys.items():
                entry = settings.providers.get(merchant)
                if entry is not None and entry.api_key is None:
                    settings.providers[merchant] = entry.model_copy(update={'api_key': key})
                elif entry is None and (provider := self.catalog.providers.get(merchant)) is not None:
                    settings.providers[merchant] = AdminProviderEntry(model_name=provider.default_model, api_key=key)
            self._write(bindings, settings)
            return self.get()

    def update_model(self, selection: ModelSettings, key: SecretStr, *, replace_key: bool,
                     keep_address: bool = False) -> bool:
        with self.lock:
            bindings, values = self._read()
            try:
                providers = self._entries(values)
            except ValueError:
                if not replace_key or _PROVIDERS in values or _MODELS in values:
                    raise
                providers = {}
            settings = AdminModelFile(api_keys=_provider_keys(values), providers=providers)
            merchant, name = selection.model_merchant, selection.model_name
            # 未输入新密钥时，确认连接测试后该厂商的密钥没有被另一个请求替换。
            if not replace_key and settings.key_for(merchant) != key:
                return False
            if keep_address:
                entry = settings.providers.get(merchant)
                if (entry.base_url if entry else '') != selection.base_url:
                    return False
            settings.model = selection
            settings.providers[merchant] = AdminProviderEntry(model_name=name, base_url=selection.base_url, api_key=key)
            self._write(bindings, settings)
            return True

    def _write(self, bindings, settings: AdminModelFile) -> None:
        model = settings.model
        entries = {merchant: {'model_name': entry.model_name, 'base_url': entry.base_url,
                              'api_key': entry.api_key.get_secret_value() if entry.api_key else None}
                   for merchant, entry in settings.providers.items()}
        fields = {
            _MERCHANT: model.model_merchant if model else '',
            _PROVIDERS: json.dumps(entries, ensure_ascii=False, separators=(',', ':')),
        }
        retired = {_MODELS, _MODEL_NAME, _BASE_URL}
        for merchant, name in _PROVIDER_KEYS.items():
            key = settings.api_keys.get(merchant)
            entry = settings.providers.get(merchant)
            if key is not None and entry is not None and entry.api_key == key:
                retired.add(name)
            elif key is not None:
                fields[name] = key.get_secret_value()

        # 只重写管理员字段，保留其余变量与注释。一次替换避免部分配置已更新。
        raw = next((binding.value for binding in reversed(bindings) if binding.key == _MODELS), None)
        if raw and any(len(entries) > 1 for entries in _MODELS_FORMAT.validate_json(raw).values()):
            backup_provider_migration(self.path, ''.join(binding.original.string for binding in bindings))
        other = ''.join(binding.original.string for binding in bindings
                        if binding.key not in fields and binding.key not in retired).rstrip('\n')
        output = [other + '\n\n'] if other else []
        output.extend(dotenv_assignment(name, value) for name, value in fields.items())
        atomic_write_text(self.path, ''.join(output))
