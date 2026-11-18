"""Default model choices and endpoints configured by the server operator."""

import json
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import BACKEND_ROOT


ModelMerchant = Literal['deepseek', 'tongyi']
ModelAdapter = Literal['openai', 'dashscope']


def validate_base_url(value: str) -> str:
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ValueError('接口地址须为有效的 HTTP(S) 地址') from None
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('接口地址须为不含账号、参数或片段的 HTTP(S) 地址')
    try:
        parsed.port
    except ValueError:
        raise ValueError('接口地址端口无效') from None
    return value.rstrip('/')


BaseURL = Annotated[str, Field(max_length=2000), AfterValidator(validate_base_url)]


class _CatalogEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)


class ModelEndpoint(_CatalogEntry):
    base_url: BaseURL = Field(min_length=1)
    adapter: ModelAdapter


class ProviderCatalog(ModelEndpoint):
    default_model: str = Field(min_length=1, max_length=200)
    models: list[str]

    @field_validator('models', mode='before')
    @classmethod
    def read_legacy_models(cls, value):
        return list(value) if isinstance(value, dict) else value

    @model_validator(mode='after')
    def check_default_model(self):
        if self.default_model not in self.models:
            raise ValueError('默认模型须出现在该厂商的 models 中')
        if any(not name or name != name.strip() or len(name) > 200 for name in self.models):
            raise ValueError('系统默认模型列表包含无效模型名')
        if len(set(self.models)) != len(self.models):
            raise ValueError('系统模型列表包含重复模型名')
        return self


class ModelCatalog(_CatalogEntry):
    default_provider: ModelMerchant
    providers: dict[ModelMerchant, ProviderCatalog]

    @model_validator(mode='after')
    def check_default_provider(self):
        if self.default_provider not in self.providers:
            raise ValueError('默认厂商须出现在 providers 中')
        return self

    def allows(self, merchant: ModelMerchant, name: str) -> bool:
        provider = self.providers.get(merchant)
        return provider is not None and name in provider.models

    def endpoint(self, merchant: ModelMerchant) -> ModelEndpoint:
        provider = self.providers.get(merchant)
        if provider is None:
            raise ValueError('全局配置缺少该厂商地址')
        return ModelEndpoint(base_url=provider.base_url, adapter=provider.adapter)


def default_model_catalog() -> ModelCatalog:
    # 示例文件提供新安装和旧全局配置缺少 models 时的初始目录。
    example = json.loads((BACKEND_ROOT / 'system_config.example.json').read_text(encoding='utf-8'))
    return ModelCatalog.model_validate(example['models'])


def migration_model_name(entries_with_keys: dict[str, bool], active: str | None,
                         default: str | None = None) -> str:
    # 保留当前模型；其余厂商优先保留有密钥的记录。
    return max(entries_with_keys, key=lambda name: (name == active, entries_with_keys[name], name == default))
