from fastapi import APIRouter

from core.config import VERSION

router = APIRouter()


# 获取版本等信息
@router.get("/version")
async def version():
    return {
        "version": VERSION,
    }
