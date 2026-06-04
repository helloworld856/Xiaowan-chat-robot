from fastapi.responses import JSONResponse
from fastapi import APIRouter

router = APIRouter()

# 处理chrome的DevTools请求
@router.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools_probe():
    """
    处理 Chrome DevTools 探测请求
    """
    return JSONResponse(
        content={"message": "Chrome DevTools probe"},
        status_code=200
    )