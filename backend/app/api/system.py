from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import VERSION

router = APIRouter()


# 获取版本等信息
@router.get("/version")
async def version():
    return {
        "version": VERSION,
    }


# 保留 Chrome DevTools 的探测地址，方便本地调试。
@router.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools_probe():
    """
    处理 Chrome DevTools 探测请求
    """
    return JSONResponse(
        content={"message": "Chrome DevTools probe"},
        status_code=200
    )
