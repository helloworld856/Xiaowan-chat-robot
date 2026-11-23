from app.account_logging import configure_logging
from app.application import build_application
from app.config import BACKEND_ROOT, PROJECT_ROOT
from app.database.mysql import open_pool
from app.static_assets import SiteStaticFiles
from app.system_config import load_system_config


config = load_system_config(migrate=True)
configure_logging(config.logging)
app = build_application(open_pool(config=config.database), None, BACKEND_ROOT / 'auth_config', system_config=config)
# 静态站点挂在最后，已注册的 API 优先匹配。
app.mount('/', SiteStaticFiles(directory=PROJECT_ROOT / 'frontend' / 'dist', html=True), name='static')
