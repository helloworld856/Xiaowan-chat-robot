"""
FastAPI 实例 + 中间件
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

def create_app(version: str) -> FastAPI:
    app = FastAPI(version=version)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app
