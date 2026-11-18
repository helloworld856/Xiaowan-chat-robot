from starlette.staticfiles import StaticFiles

from app.account_logging import configure_logging
from app.application import build_application
from app.config import BACKEND_ROOT, PROJECT_ROOT
from app.database.mysql import open_pool
from app.system_config import load_system_config


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.update({
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })
        return response

config = load_system_config(migrate=True)
configure_logging(config.logging)
app = build_application(open_pool(config=config.database), None, BACKEND_ROOT / 'auth_config', system_config=config)
# 静态站点挂在最后，已注册的 API 优先匹配。
app.mount('/', NoCacheStaticFiles(directory=PROJECT_ROOT / 'frontend' / 'dist', html=True), name='static')
