"""Read and document the global configuration for one backend process."""

import json
import os
from collections import ChainMap
from io import StringIO
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.accounts.files import protect_file, read_text
from app.config import BACKEND_ROOT
from app.json_files import (atomic_write_text, backup_provider_migration, dotenv_assignment,
                            remove_json_section_field, strip_json_line_comments)
from app.llm.model_catalog import ModelCatalog, default_model_catalog

MAX_PASSWORD_INPUT_LENGTH = 256


class _ConfigSection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class DatabaseConfig(_ConfigSection):
    host: str = Field(default='localhost', min_length=1, description='MySQL 主机名或 IP。')
    port: int = Field(default=3306, ge=1, le=65535, description='MySQL 端口。')
    user: str = Field(default='root', min_length=1, description='MySQL 登录用户名。')
    password: SecretStr = Field(default=SecretStr(''), exclude=True)
    name: str = Field(default='XiaoWan', pattern=r'^[A-Za-z0-9_]+$', description='数据库名称，允许字母、数字和下划线。')
    charset: str = Field(default='utf8mb4', min_length=1, description='MySQL 连接字符集。')


class ServerConfig(_ConfigSection):
    host: str = Field(default='127.0.0.1', min_length=1, description='后端监听地址。使用本机反向代理时通常填 127.0.0.1。')
    port: int = Field(default=8000, ge=1, le=65535, description='后端监听端口。修改后同步调整反向代理。')


class ChatConfig(_ConfigSection):
    concurrency: int = Field(default=4, ge=1, description='可同时执行的聊天请求数。')
    queue_size: int = Field(default=8, ge=1, description='等待处理的聊天请求名额。')
    queue_timeout: int = Field(default=2, ge=1, description='聊天排队最长等待时间，单位为秒。')
    max_message_chars: int = Field(default=8000, ge=1, description='单条用户消息最多允许的字符数。')
    history_compression_tokens: int = Field(default=100000, ge=1, description='历史对话估算 token 数超过该值时触发摘要压缩。')
    clear_history_on_startup: bool = Field(default=True, description='设为 true 时，后端启动会清空所有账号的聊天历史和摘要。')
    clear_learned_portrait_on_startup: bool = Field(
        default=True,
        description='设为 true 时，后端启动会清空所有账号用户画像中自动记录的印象（保留手动填写的资料）。',
    )


class PasswordConfig(_ConfigSection):
    min_length: int = Field(default=6, ge=1, le=MAX_PASSWORD_INPUT_LENGTH, description='注册或改密时，新密码的最少字符数。')
    max_length: int = Field(default=15, ge=1, le=MAX_PASSWORD_INPUT_LENGTH, description='注册或改密时，新密码的最多字符数，上限为 256。')

    @model_validator(mode='after')
    def check_length_range(self):
        if self.min_length > self.max_length:
            raise ValueError('密码最小长度不能超过最大长度')
        return self


class RateLimitConfig(_ConfigSection):
    window_seconds: int = Field(default=60, ge=1, description='认证限流的统计窗口，单位为秒。')
    login_attempts: int = Field(default=5, ge=1, description='每个客户端在窗口内的登录与改密失败额度，两者共用。')
    registration_attempts: int = Field(default=5, ge=1, description='每个客户端在窗口内的注册额度。')
    max_clients: int = Field(default=4096, ge=1, description='每个限流器最多跟踪的客户端数。')


class AuthConfig(_ConfigSection):
    session_ttl_seconds: int = Field(default=28800, ge=1, description='登录 Cookie 和服务端会话的有效期，单位为秒。28800 秒等于 8 小时。')
    password: PasswordConfig = Field(default_factory=PasswordConfig, description='注册和改密使用的密码长度规则。')
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig, description='按客户端统计的认证限流。')


AvatarId = Literal['cat', 'fox', 'rabbit', 'bear', 'penguin', 'frog']


class ProfileConfig(_ConfigSection):
    max_nickname_chars: int = Field(default=32, ge=1, le=256, description='网页保存昵称时允许的最多字符数，上限为 256。')


class DefaultsConfig(_ConfigSection):
    chat_font_size: Literal['small', 'standard', 'large'] = Field(
        default='standard', description='新账号聊天字号。可选 small、standard 或 large。',
    )
    send_shortcut: Literal['enter', 'ctrl_enter'] = Field(default='enter', description='新账号发送快捷键。可选 enter 或 ctrl_enter。')
    theme: Literal['theme0', 'theme1', 'theme2', 'theme3', 'theme4'] = Field(
        default='theme0',
        description='新账号主题。theme0 默认白，theme1 海阔天空，theme2 夏日海岸，theme3 樱雨绵绵，theme4 晚风夜色。',
    )
    avatar_id: AvatarId = Field(default='cat', description='新账号头像。可选 cat、fox、rabbit、bear、penguin、frog。')


class ModelRuntimeConfig(_ConfigSection):
    validation_concurrency: int = Field(default=2, ge=1, description='管理员与普通用户共用的并行模型连接验证名额。')


class LoggingConfig(_ConfigSection):
    max_bytes: int = Field(default=2000000, ge=1, description='单个系统或账号日志达到多少字节时轮转。')
    backup_count: int = Field(default=3, ge=1, description='每个日志最多保留的轮转归档份数。')
    max_open_account_logs: int = Field(default=32, ge=1, description='后端最多同时打开多少个账号日志文件。')
    clear_on_startup: bool = Field(default=True, description='设为 true 时，启动会清空当前系统和账号日志，保留轮转归档。')


class SystemConfig(_ConfigSection):
    database: DatabaseConfig = Field(default_factory=DatabaseConfig, description='MySQL 连接设置。密码由 .env 的 MYSQL_PASSWORD 提供。')
    server: ServerConfig = Field(default_factory=ServerConfig, description='小晚后端监听设置。')
    chat: ChatConfig = Field(default_factory=ChatConfig, description='聊天队列、输入与历史设置。')
    models: ModelCatalog = Field(default_factory=default_model_catalog, description='厂商默认地址、协议和可选模型。')
    auth: AuthConfig = Field(default_factory=AuthConfig, description='登录会话、密码规则与认证限流。')
    profile: ProfileConfig = Field(default_factory=ProfileConfig, description='个人资料的校验规则。')
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig, description='新账号的初始偏好和头像。')
    model_runtime: ModelRuntimeConfig = Field(default_factory=ModelRuntimeConfig, description='模型配置的运行限制。')
    logging: LoggingConfig = Field(default_factory=LoggingConfig, description='系统日志与账号日志的轮转和启动清理。')


def legacy_environment(root: Path) -> ChainMap:
    # 旧启动方式优先使用进程环境，迁移沿用这个优先级。
    return ChainMap(os.environ, dotenv_values(root / '.env'))


def _with_database_password(settings: SystemConfig, env_path: Path, *, migrate: bool) -> SystemConfig:
    try:
        if migrate:
            protect_file(env_path)
        try:
            text = read_text(env_path)
        except FileNotFoundError:
            text = None
        password = dotenv_values(stream=StringIO(text or ''), interpolate=False).get('MYSQL_PASSWORD')
        if password is None:
            if 'password' in settings.database.model_fields_set:
                password = settings.database.password.get_secret_value()
            else:
                password = legacy_environment(env_path.parent).get('PASSWORD')
            if migrate and password is not None:
                # 先写入并确认 .env 可以读回密码，之后才能移除旧字段。
                original = text or ''
                separator = '\n' if original and not original.endswith('\n') else ''
                updated = original + separator + dotenv_assignment('MYSQL_PASSWORD', password)
                if dotenv_values(stream=StringIO(updated), interpolate=False).get('MYSQL_PASSWORD') != password:
                    raise ValueError('数据库密码无法写入 .env')
                if text is not None and read_text(env_path) != text:
                    raise OSError('.env 在读取后已被更新')
                atomic_write_text(env_path, updated, replace=text is not None)
                if dotenv_values(stream=StringIO(read_text(env_path)), interpolate=False).get('MYSQL_PASSWORD') != password:
                    raise ValueError('数据库密码无法从 .env 读回')
    except (OSError, ValueError):
        raise ValueError('无法读取或迁移 MySQL 密码，请检查 backend/.env 的格式和权限') from None
    database = settings.database.model_copy(update={'password': SecretStr(password or '')})
    return settings.model_copy(update={'database': database})


def _format_config_object(data: dict, source, depth: int = 0) -> str:
    lines = ['{']
    indent = '  ' * (depth + 1)
    for index, (key, value) in enumerate(data.items(), 1):
        if isinstance(source, BaseModel):
            comment = type(source).model_fields[key].description
            child = getattr(source, key)
        else:
            comment = f'{key} 厂商配置。'
            child = source[key]
        if comment:
            lines.append(f'{indent}// {comment}')
        rendered = _format_config_object(value, child, depth + 1) if isinstance(value, dict) else json.dumps(
            value, ensure_ascii=False,
        )
        comma = ',' if index < len(data) else ''
        lines.append(f'{indent}{json.dumps(key)}: {rendered}{comma}')
    lines.append('  ' * depth + '}')
    return '\n'.join(lines)


def format_system_config(settings: SystemConfig) -> str:
    data = settings.model_dump(mode='json')
    return '// 小晚全局配置。修改后重启后端生效。\n' + _format_config_object(data, settings) + '\n'


def _write_system_config(path: Path, settings: SystemConfig, *, replace: bool) -> None:
    atomic_write_text(path, format_system_config(settings), replace=replace)


def load_system_config(root: Path | None = None, *, migrate: bool = False) -> SystemConfig:
    root = root if root is not None else BACKEND_ROOT
    path = root / 'system_config.json'
    try:
        if migrate:
            protect_file(path)
        text = read_text(path)
        json_text = strip_json_line_comments(text)
        settings = SystemConfig.model_validate_json(json_text)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        raise ValueError('无法读取 system_config.json，请检查文件格式、字段或权限') from None
    else:
        settings = _with_database_password(settings, root / '.env', migrate=migrate)
        if migrate:
            original = json.loads(json_text)
            scrubbed = remove_json_section_field(text, 'database', 'password')
            if scrubbed != text:
                scrubbed = scrubbed.replace('    // MySQL 登录密码，只保存在服务器上。\n', '')
            previous = original.get('models', {}).get('providers', {})
            old_lists = [provider['models'] for provider in previous.values() if isinstance(provider['models'], dict)]
            if any(any(any(value is not None for value in model.values()) for model in names.values() if isinstance(model, dict))
                   for names in old_lists):
                backup_provider_migration(path, scrubbed)
            # 纯 JSON 旧配置补齐字段和说明，已经带注释的文件保留手写注释。
            if old_lists or json_text == text:
                _write_system_config(path, settings, replace=True)
            elif scrubbed != text:
                atomic_write_text(path, scrubbed)
        return settings

    try:
        legacy = legacy_environment(root)
        defaults = ChatConfig()
        settings = SystemConfig(
            chat=ChatConfig(
                concurrency=int(legacy.get('XIAOWAN_CHAT_CONCURRENCY', defaults.concurrency)),
                queue_size=int(legacy.get('XIAOWAN_CHAT_QUEUE_SIZE', defaults.queue_size)),
                queue_timeout=int(legacy.get('XIAOWAN_CHAT_QUEUE_TIMEOUT', defaults.queue_timeout)),
            ),
        )
    except (OSError, TypeError, ValueError):
        raise ValueError('无法读取旧版数据库或并发配置，请检查 .env 或创建 system_config.json') from None

    settings = _with_database_password(settings, root / '.env', migrate=migrate)
    if migrate:
        try:
            _write_system_config(path, settings, replace=False)
        except FileExistsError:
            # 另一进程已完成首次写入，以磁盘上的配置为准。
            return load_system_config(root)
    return settings
