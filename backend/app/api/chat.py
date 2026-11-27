from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_serializer

from app.account_logging import logger
from app.chat.progress import ChatCancelled
from app.chat.scheduler import ChatNotStarted

TURN_ID_PATTERN = r'^[A-Za-z0-9_-]{1,64}$'


class ChatRequest(BaseModel):
    user_input: str
    turn_id: str | None = Field(default=None, pattern=TURN_ID_PATTERN)


class CancelChatRequest(BaseModel):
    turn_id: str | None = Field(default=None, pattern=TURN_ID_PATTERN)


class ChatResponse(BaseModel):
    response: list
    conversation_round: int | None
    saved: bool
    created_at: int | None = None

    @model_serializer
    def _serialize(self) -> dict:
        payload = {
            'response': self.response,
            'conversation_round': self.conversation_round,
            'saved': self.saved,
        }
        if self.created_at is not None:
            payload['created_at'] = self.created_at
        return payload


router = APIRouter()


def _validate_user_input(request: Request, user_input: str) -> None:
    limit = request.app.state.system_config.chat.max_message_chars
    if len(user_input) > limit:
        raise ChatNotStarted(422, f'消息最多可输入 {limit} 个字符')
    if not user_input.strip():
        raise ChatNotStarted(422, '请输入消息内容')


def _build_response(result: dict) -> ChatResponse:
    message = result.get('message') or {}
    return ChatResponse(
        response=result['ai_response'],
        conversation_round=message.get('conversation_round') if result['saved'] else None,
        saved=result['saved'],
        created_at=message.get('created_at'),
    )


def _raise_not_started(error: ChatNotStarted):
    raise HTTPException(
        error.status_code,
        detail={'code': 'CHAT_NOT_STARTED', 'message': str(error)},
    ) from None


@router.post('/chat', response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    try:
        _validate_user_input(request, req.user_input)
        # 调度使用认证后的账号，带 turn_id 时支持主动取消且取消后不落库。
        if req.turn_id is None:
            result = await request.app.state.chat.send(request.state.user, req.user_input)
        else:
            result = await request.app.state.chat.send(request.state.user, req.user_input, turn_id=req.turn_id)
    except ChatCancelled:
        raise HTTPException(499, detail={'code': 'CHAT_CANCELLED', 'message': '已停止本次回复'}) from None
    except ChatNotStarted as error:
        _raise_not_started(error)
    except Exception as error:
        logger.error('chat execution failed: %s', type(error).__name__)
        raise HTTPException(500, 'AI 处理失败，请稍后重试') from None

    return _build_response(result)


@router.post('/chat/cancel')
async def cancel_chat(req: CancelChatRequest, request: Request):
    cancelled = request.app.state.chat.cancel(request.state.user.id, req.turn_id)
    return {'ok': True, 'cancelled': cancelled}
