from starlette.staticfiles import StaticFiles

from app.application import build_application
from app.config import BACKEND_ROOT, PROJECT_ROOT
from database_config import open_pool
from profile_config import ProfileStore


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.update({
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })
        return response

app = build_application(open_pool(), ProfileStore(), BACKEND_ROOT / 'auth_config')
app.mount('/', NoCacheStaticFiles(directory=PROJECT_ROOT / 'frontend' / 'dist', html=True), name='static')
