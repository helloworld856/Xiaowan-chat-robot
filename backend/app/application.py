from contextlib import asynccontextmanager
from functools import partial
from logging import ERROR
from pathlib import Path

from dotenv import dotenv_values
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.account_logging import close_account_logs, log_event, logger
from app.accounts.auth_service import AuthService, hash_password
from app.accounts.directory import UserDirectoryStore
from app.accounts.files import UserDataPaths, protect_file
from app.accounts.preferences import UserPreferencesStore
from app.accounts.profile import ProfileStore
from app.accounts.repository import SQLAccountStore
from app.api import attachments, chat, history, model, persona, portrait, settings, system
from app.api.auth import router as auth_router
from app.api.auth_middleware import AuthenticationMiddleware
from app.api.profile import router as profile_router
from app.chat.episodes import EpisodeMemoryStore
from app.chat.scheduler import ChatSessions
from app.chat.workflow import create_graph
from app.config import BACKEND_ROOT, SOFTWARE_NAME, VERSION
from app.database.mysql import initialize_schema, reset_chat_history, reset_episode_memories
from app.llm.admin_models import AdminModelStore, import_legacy_admin_model
from app.llm.admin_settings import ModelSettings, settings_store
from app.llm.configuration import AdminModelRuntime, ModelConfigurationService
from app.llm.embeddings import LocalEmbeddingModel
from app.llm.model_catalog import ModelCatalog
from app.system_config import ChatConfig, SystemConfig, legacy_environment
from tools_config.search_tool.searxng import searxng_router


INITIAL_ADMIN_PASSWORD_MIN_LENGTH = 6
INITIAL_ADMIN_PASSWORD_MAX_LENGTH = 18


# 装配通用认证与资料接口，测试可以注入不依赖真实 MySQL 的存储。
def create_app(version: str, auth_service: AuthService | None = None,
               profile_store: ProfileStore | None = None, lifespan=None,
               *, user_data_paths: UserDataPaths | None = None,
               system_config: SystemConfig | None = None) -> FastAPI:
    config = system_config or SystemConfig()
    app = FastAPI(title=SOFTWARE_NAME, version=version, lifespan=lifespan)
    app.state.system_config = config
    app.state.auth = auth_service or AuthService(config=config.auth)
    app.state.profile = profile_store if profile_store is not None else ProfileStore(
        config=config.profile, defaults=config.defaults,
    )
    app.add_middleware(AuthenticationMiddleware, service=app.state.auth, user_data_paths=user_data_paths)
    app.include_router(auth_router)
    app.include_router(profile_router)
    return app


def build_application(db, profile_store, legacy_root, *, chat_config: ChatConfig | None = None,
                      model_catalog: ModelCatalog | None = None, config_root: Path | None = None,
                      system_config: SystemConfig | None = None,
                      prepare_embedding: bool | None = None):
    config = system_config or SystemConfig()
    if chat_config is not None:
        config = config.model_copy(update={'chat': chat_config})
    chat_config = config.chat
    if profile_store is None:
        profile_store = ProfileStore(config=config.profile, defaults=config.defaults)
    config_root = config_root if config_root is not None else legacy_root.parent
    should_prepare_embedding = (config_root == BACKEND_ROOT) if prepare_embedding is None else prepare_embedding
    env_path = config_root / '.env'
    legacy_preferences = UserPreferencesStore(db)
    directories = UserDirectoryStore(profile_store, legacy_preferences, model_catalog or config.models,
                                     defaults=config.defaults)
    accounts = SQLAccountStore(db, prepare_user=directories.prepare_new)
    admin_model = AdminModelRuntime()
    model_configuration = ModelConfigurationService(
        AdminModelStore(env_path, directories.models.catalog), directories.models,
        admin_model, config=config.model_runtime,
    )
    embedding_model = LocalEmbeddingModel(config.memory.embedding, root=config_root)
    episode_store = EpisodeMemoryStore(db.connection, config=config.memory, embedder=embedding_model)

    def startup():
        logger.info("version:%s", VERSION)
        initialize_schema(db)
        log_event('db_schema_ready', system=True)
        admin = accounts.get_admin()
        if admin is None:
            # 只在空库读取初始密码，关闭变量展开以保留密码里的特殊字符。
            try:
                env = dotenv_values(env_path, interpolate=False) if protect_file(env_path) else {}
            except (OSError, UnicodeError):
                raise RuntimeError('无法读取 backend/.env 中的 ADMIN_INITIAL_PASSWORD') from None
            password = env.get('ADMIN_INITIAL_PASSWORD') or ''
            if (not password.strip()
                    or not INITIAL_ADMIN_PASSWORD_MIN_LENGTH <= len(password) <= INITIAL_ADMIN_PASSWORD_MAX_LENGTH):
                raise RuntimeError('尚未初始化管理员，请在 backend/.env 中填写 '
                                   f'{INITIAL_ADMIN_PASSWORD_MIN_LENGTH}–{INITIAL_ADMIN_PASSWORD_MAX_LENGTH} '
                                   '个字符的 ADMIN_INITIAL_PASSWORD，再启动后端')
            admin = accounts.initialize_admin(hash_password(password))
            log_event('admin_initialized', system=True, user_id=admin.id)
        directories.recover_deletions(accounts)
        directories.migrate_legacy(accounts)
        directories.paths.bind(admin)
        try:
            profile_store.import_legacy_profile(admin.id, legacy_root / 'profile.json')
        except (OSError, ValueError):
            logger.warning('无法导入旧版管理员资料，可在个人资料中重新保存')
        legacy_model = None
        try:
            saved = settings_store.get()
        except (OSError, ValueError):
            logger.warning('无法读取旧版管理员配置')
        else:
            if saved is None:
                # 新安装从全局配置取默认模型，旧文件存在时才迁移其中的偏好。
                catalog = directories.models.catalog
                merchant = catalog.default_provider
                legacy_model = ModelSettings(model_merchant=merchant, model_name=catalog.providers[merchant].default_model)
            else:
                legacy_model = saved.model
                legacy_preferences.import_legacy(admin.id, saved.preferences)
        try:
            directories.ensure(admin)
        except (OSError, ValueError):
            logger.warning('无法初始化管理员的个人目录，请检查文件权限与格式')
        if chat_config.clear_history_on_startup:
            try:
                reset_chat_history(db)
                directories.attachments.clear_all()
            except Exception as error:
                log_event('startup_history_clear', level=ERROR, system=True, outcome='failed',
                          error=type(error).__name__)
                raise
            log_event('startup_history_clear', system=True, outcome='succeeded')
        if chat_config.clear_learned_portrait_on_startup:
            try:
                cleared_portraits = directories.portraits.clear_all_learned()
            except Exception as error:
                log_event('startup_portrait_learned_clear', level=ERROR, system=True, outcome='failed',
                          error=type(error).__name__)
                raise
            log_event('startup_portrait_learned_clear', system=True, outcome='succeeded',
                      accounts=cleared_portraits)
        if config.memory.clear_episodes_on_startup:
            try:
                reset_episode_memories(db)
            except Exception as error:
                log_event('startup_episodes_clear', level=ERROR, system=True, outcome='failed',
                          error=type(error).__name__)
                raise
            log_event('startup_episodes_clear', system=True, outcome='succeeded')
        model_configuration.activate_saved_model(lambda: import_legacy_admin_model(
            legacy_model, legacy_environment(config_root),
            previous=directories.paths.file(admin.id, 'model.json'),
        ))
        if should_prepare_embedding:
            embedding_model.prepare_on_startup()
            searxng_router.prepare_on_startup(config.search, project_root=config_root.parent)
        else:
            searxng_router.configure(config.search)

    @asynccontextmanager
    async def lifespan(_):
        try:
            await run_in_threadpool(startup)
            yield
        finally:
            embedding_model.close()
            searxng_router.close()
            try:
                # 先等已接收的聊天落库，再关闭它们依赖的数据库和日志。
                await application.state.chat.drain()
            finally:
                try:
                    await run_in_threadpool(model_configuration.close)
                finally:
                    try:
                        await run_in_threadpool(db.close)
                    finally:
                        close_account_logs(directories.paths)

    auth = AuthService(account_store=accounts, config=config.auth,
                       delete_account=partial(directories.delete_account, accounts), restore_user=directories.restore_user)
    application = create_app(VERSION, auth, profile_store,
                             lifespan=lifespan, user_data_paths=directories.paths, system_config=config)
    application.state.db = db
    application.state.preferences = directories.preferences
    application.state.portraits = directories.portraits
    application.state.attachments = directories.attachments
    application.state.episodes = episode_store
    application.state.personal_models = directories.models
    application.state.model_configuration = model_configuration
    application.state.chat = ChatSessions(
        db.connection,
        personal_models=directories.models,
        admin_model=admin_model,
        portraits=directories.portraits,
        attachments=directories.attachments,
        episodes=episode_store,
        search_config=config.search,
        graph_factory=partial(
            create_graph,
            compression_tokens=chat_config.history_compression_tokens,
            episodes=episode_store,
            recent_window_rounds=config.memory.recent_window_rounds,
        ),
        max_running=chat_config.concurrency,
        max_waiting=chat_config.queue_size,
        wait_timeout=chat_config.queue_timeout,
    )
    for module in (persona, portrait, attachments, system, settings, chat, model, history):
        application.include_router(module.router)
    return application
