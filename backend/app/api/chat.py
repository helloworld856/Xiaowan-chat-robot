from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.account_logging import logger
from app.chat.scheduler import ChatNotStarted


class ChatRequest(BaseModel):
    user_input: str


class ChatResponse(BaseModel):
    response: list
    conversation_round: int | None
    saved: bool


router = APIRouter()


@router.post('/chat', response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    try:
        limit = request.app.state.system_config.chat.max_message_chars
        if len(req.user_input) > limit:
            raise ChatNotStarted(422, f'消息最多可输入 {limit} 个字符')
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
        conversation_round=result['message']['conversation_round'] if result['saved'] else None,
        saved=result['saved'],
    )
