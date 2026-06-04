from core.config import VERSION
from fastapi import  APIRouter

router = APIRouter()

# 获取版本等信息
@router.get("/version")
async def version():
    return {
        "version": VERSION,
    }