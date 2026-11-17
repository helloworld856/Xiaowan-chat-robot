from fastapi import APIRouter, HTTPException, Request

from app.chat.scheduler import ChatNotStarted
from app.account_logging import logger

from pydantic import BaseModel
from typing import List, Optional


class ChatRequest(BaseModel):
    user_input: str

class ChatResponse(BaseModel):
    response: List
    conversation_round: Optional[int]
    saved: bool


router = APIRouter()
MAX_MESSAGE_LENGTH = 8000


@router.post('/chat', response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    try:
        if len(req.user_input) > MAX_MESSAGE_LENGTH:
            raise ChatNotStarted(422, f'消息最多可输入 {MAX_MESSAGE_LENGTH} 个字符')
        if not req.user_input.strip():
            raise ChatNotStarted(422, '请输入消息内容')
        # 调度使用认证后的账号，消息体只提供聊天内容。
        result = await request.app.state.chat.send(request.state.user, req.user_input)
    except ChatNotStarted as error:
        raise HTTPException(
            error.status_code,
            detail={'code': 'CHAT_NOT_STARTED', 'message': str(error)},
        ) from None
    except Exception as error:
        logger.error('chat execution failed: %s', type(error).__name__)
        raise HTTPException(500, 'AI 处理失败，请稍后重试') from None

    return ChatResponse(
        response=result['ai_response'],
        conversation_round=result['message']['conversation_round'] - 1,
        saved=result['saved'],
    )
