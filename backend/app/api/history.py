from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.account_logging import logger
from app.chat.memory import delete_history_round, load_history
from app.chat.scheduler import ChatNotStarted
from app.database.mysql import reset_chat_history


MAX_HISTORY_MESSAGES = 10_000


class HistoryRequest(BaseModel):
    num: int | None = Field(
        ge=-MAX_HISTORY_MESSAGES, le=MAX_HISTORY_MESSAGES,
        description=f'正数按 front 指定方向读取，负数反向读取绝对值条数，0 或 null 返回最近 {MAX_HISTORY_MESSAGES} 条以内的记录。',
    )
    front: bool = Field(description='为 true 时基础顺序是从最早到最新，否则从最新到最早。')


class HistoryResponse(BaseModel):
    num: int | None = Field(description='原样回显请求的 num，实际返回条数为 history_messages 的长度。')
    history_messages: list[dict]


router = APIRouter()


@router.post("/history", response_model=HistoryResponse)
def history(req: HistoryRequest, request: Request):
    try:
        # 账号 ID 来自中间件，请求体只控制历史记录的读取范围。
        history_messages = load_history(
            request.app.state.db.connection, request.state.user.id, req.num or MAX_HISTORY_MESSAGES,
            req.front if req.num else False,
        )
        # 刷新历史时总是取最新的记录，再按请求顺序显示。
        if not req.num and req.front:
            history_messages.reverse()
    except Exception as error:
        logger.error('history read failed: %s', type(error).__name__)
        raise HTTPException(500, '读取聊天记录失败，请稍后重试') from None
    return HistoryResponse(
        num=req.num,
        history_messages=history_messages,
    )


@router.delete('/history')
async def clear_history(request: Request):
    user_id = request.state.user.id
    try:
        async with request.app.state.chat.maintenance(user_id):
            await run_in_threadpool(reset_chat_history, request.app.state.db, user_id)
    except ChatNotStarted as error:
        raise HTTPException(error.status_code, str(error)) from None
    except Exception as error:
        logger.error('history clear failed: %s', type(error).__name__)
        raise HTTPException(500, '清空聊天记录失败，请稍后重试') from None
    return {'ok': True}


@router.delete('/history/{conversation_round}')
async def delete_single_history(request: Request, conversation_round: int = Path(ge=0)):
    user_id = request.state.user.id
    try:
        async with request.app.state.chat.maintenance(user_id):
            removed = await run_in_threadpool(
                delete_history_round, request.app.state.db.connection, user_id, conversation_round,
            )
    except ChatNotStarted as error:
        raise HTTPException(error.status_code, str(error)) from None
    except Exception as error:
        logger.error('single history delete failed: %s', type(error).__name__)
        raise HTTPException(500, '删除该轮对话失败，请稍后重试') from None
    if not removed:
        raise HTTPException(404, '该轮对话不存在或已归入摘要，无法单独删除')
    return {'ok': True}
