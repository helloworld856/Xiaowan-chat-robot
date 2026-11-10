"""
FastAPI 实例 + 中间件
"""

from fastapi import FastAPI
from .config import SOFTWARE_NAME
from .auth import AuthenticationMiddleware
from auth_config import AuthService
from profile_config import ProfileStore
from routers.auth import router as auth_router
from routers.profile import router as profile_router

def create_app(version: str, auth_service: AuthService | None = None,
               profile_store: ProfileStore | None = None) -> FastAPI:
    app = FastAPI(title=SOFTWARE_NAME, version=version)
    app.state.auth = auth_service or AuthService()
    app.state.profile = profile_store if profile_store is not None else ProfileStore()
    app.add_middleware(AuthenticationMiddleware, service=app.state.auth)
    app.include_router(auth_router)
    app.include_router(profile_router)
    return app
