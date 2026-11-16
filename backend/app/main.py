import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.concurrency import run_in_threadpool
# 获取当前目录路径
current_dir = os.path.dirname((os.path.abspath(__file__)))
sys.path.append(current_dir)

from core.app import create_app
from core.config import VERSION
from core.session import ChatSessions
from routers import chat, persona, model, misc, history, version, settings
from static.mount import NoCacheStaticFiles

from config import configer
from auth_config import AuthService, SQLAccountStore
from database_config import initialize_schema, open_pool, reset_chat_history
from graph_config import create_graph
from log_config import logger
from personal_model_config import PersonalModelStore
from profile_config import ProfileStore
from settings_config import UserPreferencesStore, settings_store



def positive_setting(name, default):
    value = int(os.getenv(name, default))
    if value < 1:
        raise ValueError(f'{name} 必须为正整数')
    return value


def build_application(db, profile_store, legacy_root):
    personal_models = PersonalModelStore(profile_store.root)
    accounts = SQLAccountStore(db, prepare_user=personal_models.ensure)
    preferences = UserPreferencesStore(db)

    def startup():
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
            preferences.import_legacy(admin.id, saved.preferences)
        reset_chat_history(db)
        model.activate_saved_model(saved.model if saved else None)

    @asynccontextmanager
    async def lifespan(_):
        try:
            await run_in_threadpool(startup)
            yield
        finally:
            await application.state.chat.drain()
            await run_in_threadpool(db.close)

    application = create_app(VERSION, AuthService(account_store=accounts), profile_store, lifespan=lifespan)
    application.state.db = db
    application.state.preferences = preferences
    application.state.personal_models = personal_models
    application.state.chat = ChatSessions(
        db.connection,
        personal_models=personal_models,
        graph_factory=create_graph,
        max_running=positive_setting('XIAOWAN_CHAT_CONCURRENCY', 4),
        max_waiting=positive_setting('XIAOWAN_CHAT_QUEUE_SIZE', 8),
        wait_timeout=positive_setting('XIAOWAN_CHAT_QUEUE_TIMEOUT', 2),
    )
    for module in (persona, misc, version, settings, chat, model, history):
        application.include_router(module.router)
    return application


app = build_application(open_pool(), ProfileStore(), Path(configer.root_dir_path) / 'auth_config')

# 挂载静态资源
front_dir = os.path.join(
    os.path.dirname(os.path.abspath(configer.root_dir_path)),
    "frontend",
    "dist"
)
app.mount("/", NoCacheStaticFiles(directory=front_dir, html=True), name="static")
