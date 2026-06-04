from fastapi import APIRouter, HTTPException
from memory_config import memorier
from schemas.history import HistoryRequest, HistoryResponse

router = APIRouter()


@router.post("/history", response_model=HistoryResponse)
async def history(request: HistoryRequest):
    num = request.num
    front = request.front
    history_messages = memorier.load_history(num, front)

    return HistoryResponse(
        num=num,
        history_messages=history_messages,
    )
