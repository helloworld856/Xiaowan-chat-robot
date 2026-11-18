"""Database, listener and chat limits for the running application."""

import json
import os
from collections import ChainMap
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.accounts.files import protect_file, read_text
from app.config import BACKEND_ROOT
from app.json_files import atomic_write_json, backup_provider_migration
from app.llm.model_catalog import ModelCatalog, default_model_catalog


class _ConfigSection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class DatabaseConfig(_ConfigSection):
    host: str = Field(default='localhost', min_length=1)
    port: int = Field(default=3306, ge=1, le=65535)
    user: str = Field(default='root', min_length=1)
    password: SecretStr = SecretStr('')
    name: str = Field(default='XiaoWan', pattern=r'^[A-Za-z0-9_]+$')
    charset: str = Field(default='utf8mb4', min_length=1)


class ServerConfig(_ConfigSection):
    host: str = Field(default='127.0.0.1', min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)


class ChatConfig(_ConfigSection):
    concurrency: int = Field(default=4, ge=1)
    queue_size: int = Field(default=8, ge=1)
    queue_timeout: int = Field(default=2, ge=1)


class SystemConfig(_ConfigSection):
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    models: ModelCatalog = Field(default_factory=default_model_catalog)


def legacy_environment(root: Path) -> ChainMap:
    # 旧启动方式优先使用进程环境，迁移沿用这个优先级。
    return ChainMap(os.environ, dotenv_values(root / '.env'))


def _write_system_config(path: Path, settings: SystemConfig, *, replace: bool) -> None:
    data = settings.model_dump()
    data['database']['password'] = settings.database.password.get_secret_value()
    atomic_write_json(path, data, replace=replace)


def load_system_config(root: Path | None = None, *, migrate: bool = False) -> SystemConfig:
    root = root if root is not None else BACKEND_ROOT
    path = root / 'system_config.json'
    try:
        if migrate:
            protect_file(path)
        text = read_text(path)
        settings = SystemConfig.model_validate_json(text)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        raise ValueError('无法读取 system_config.json，请检查文件格式、字段或权限') from None
    else:
        if migrate:
            previous = json.loads(text).get('models', {}).get('providers', {})
            old_lists = [provider['models'] for provider in previous.values() if isinstance(provider['models'], dict)]
            if any(any(any(value is not None for value in model.values()) for model in names.values() if isinstance(model, dict))
                   for names in old_lists):
                backup_provider_migration(path, text)
            if old_lists or 'models' not in settings.model_fields_set:
                _write_system_config(path, settings, replace=True)
        return settings

    try:
        legacy = legacy_environment(root)
        defaults = ChatConfig()
        settings = SystemConfig(
            database=DatabaseConfig(password=SecretStr(legacy.get('PASSWORD') or '')),
            chat=ChatConfig(
                concurrency=int(legacy.get('XIAOWAN_CHAT_CONCURRENCY', defaults.concurrency)),
                queue_size=int(legacy.get('XIAOWAN_CHAT_QUEUE_SIZE', defaults.queue_size)),
                queue_timeout=int(legacy.get('XIAOWAN_CHAT_QUEUE_TIMEOUT', defaults.queue_timeout)),
            ),
        )
    except (OSError, TypeError, ValueError):
        raise ValueError('无法读取旧版数据库或并发配置，请检查 .env 或创建 system_config.json') from None

    if migrate:
        try:
            _write_system_config(path, settings, replace=False)
        except FileExistsError:
            # 另一进程已完成首次写入，以磁盘上的配置为准。
            return load_system_config(root)
    return settings
