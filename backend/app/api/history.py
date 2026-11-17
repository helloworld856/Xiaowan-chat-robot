from fastapi import APIRouter, HTTPException, Request
from app.chat.memory import load_history
from app.account_logging import logger

from pydantic import BaseModel
from typing import Dict, Optional, List


# 请求
class HistoryRequest(BaseModel):
    num: Optional[int]    # 请求的聊天记录数
    front: bool

# 回应
class HistoryResponse(BaseModel):
    num: Optional[int]    # 返回的聊天记录数
    history_messages: List[Optional[Dict]] # 返回的聊天记录


router = APIRouter()


@router.post("/history", response_model=HistoryResponse)
def history(req: HistoryRequest, request: Request):
    try:
        history_messages = load_history(
            request.app.state.db.connection, request.state.user.id, req.num or 0, req.front,
        )
    except Exception as error:
        logger.error('history read failed: %s', type(error).__name__)
        raise HTTPException(500, '读取聊天记录失败，请稍后重试') from None
    return HistoryResponse(
        num=req.num,
        history_messages=history_messages,
    )
