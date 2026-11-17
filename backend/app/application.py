import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.api import chat, history, model, persona, settings, system
from app.api.auth import router as auth_router
from app.api.auth_middleware import AuthenticationMiddleware
from app.api.profile import router as profile_router
from app.config import SOFTWARE_NAME, VERSION
from app.chat.scheduler import ChatSessions
from app.accounts.auth_service import AuthService
from app.accounts.repository import SQLAccountStore
from app.database.mysql import initialize_schema, reset_chat_history
from app.chat.workflow import create_graph
from app.account_logging import logger
from app.account_logging import close_account_logs
from app.accounts.profile import ProfileStore
from app.accounts.preferences import UserPreferencesStore
from app.llm.admin_settings import settings_store
from app.accounts.files import UserDataPaths
from app.accounts.directory import UserDirectoryStore


def create_app(version: str, auth_service: AuthService | None = None,
               profile_store: ProfileStore | None = None, lifespan=None,
               *, user_data_paths: UserDataPaths | None = None) -> FastAPI:
    app = FastAPI(title=SOFTWARE_NAME, version=version, lifespan=lifespan)
    app.state.auth = auth_service or AuthService()
    app.state.profile = profile_store if profile_store is not None else ProfileStore()
    app.add_middleware(AuthenticationMiddleware, service=app.state.auth, user_data_paths=user_data_paths)
    app.include_router(auth_router)
    app.include_router(profile_router)
    return app


def positive_setting(name, default):
    value = int(os.getenv(name, default))
    if value < 1:
        raise ValueError(f'{name} 必须为正整数')
    return value


def build_application(db, profile_store, legacy_root):
    legacy_preferences = UserPreferencesStore(db)
    directories = UserDirectoryStore(profile_store, legacy_preferences)
    accounts = SQLAccountStore(db, prepare_user=directories.prepare_new)

    def startup():
        logger.info("version:%s", VERSION)
        initialize_schema(db)
        admin = accounts.seed_admin(legacy_root / 'admin.json')
        try:
            profile_store.import_legacy_profile(admin.id, legacy_root / 'profile.json')
        except (OSError, ValueError):
            logger.warning('无法导入旧版管理员资料，可在个人资料中重新保存')
        try:
            saved = settings_store.get()
        except (OSError, ValueError):
            saved = None
            logger.warning('无法加载模型设置，请在管理界面重新配置')
        if saved is not None:
            legacy_preferences.import_legacy(admin.id, saved.preferences)
        try:
            directories.ensure(admin)
        except (OSError, ValueError):
            logger.warning('无法初始化管理员的个人目录，请检查文件权限与格式')
        reset_chat_history(db)
        model.activate_saved_model(saved.model if saved else None)

    @asynccontextmanager
    async def lifespan(_):
        try:
            await run_in_threadpool(startup)
            yield
        finally:
            try:
                await application.state.chat.drain()
            finally:
                try:
                    await run_in_threadpool(db.close)
                finally:
                    close_account_logs(directories.paths)

    application = create_app(VERSION, AuthService(account_store=accounts), profile_store, lifespan=lifespan,
                             user_data_paths=directories.paths)
    application.state.db = db
    application.state.preferences = directories.preferences
    application.state.personal_models = directories.models
    application.state.chat = ChatSessions(
        db.connection,
        personal_models=directories.models,
        graph_factory=create_graph,
        max_running=positive_setting('XIAOWAN_CHAT_CONCURRENCY', 4),
        max_waiting=positive_setting('XIAOWAN_CHAT_QUEUE_SIZE', 8),
        wait_timeout=positive_setting('XIAOWAN_CHAT_QUEUE_TIMEOUT', 2),
    )
    for module in (persona, system, settings, chat, model, history):
        application.include_router(module.router)
    return application
