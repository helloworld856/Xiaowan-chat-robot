from fastapi import APIRouter, HTTPException, Request
from memory_config import load_history
from log_config import logger
from schemas.history import HistoryRequest, HistoryResponse

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
