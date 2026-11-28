import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_serializer

from app.account_logging import logger
from app.chat.attachments import ATTACHMENT_ID_PATTERN, MAX_ATTACHMENTS_PER_TURN
from app.chat.progress import ChatCancelled
from app.chat.scheduler import ChatNotStarted

TURN_ID_PATTERN = r'^[A-Za-z0-9_-]{1,64}$'
STREAM_HEARTBEAT_SECONDS = 3.0
AttachmentIdField = Annotated[str, Field(pattern=ATTACHMENT_ID_PATTERN)]


class ChatRequest(BaseModel):
    user_input: str = ''
    turn_id: str | None = Field(default=None, pattern=TURN_ID_PATTERN)
    attachment_ids: list[AttachmentIdField] = Field(default_factory=list, max_length=MAX_ATTACHMENTS_PER_TURN)
    quoted_attachment_ids: list[AttachmentIdField] = Field(default_factory=list, max_length=MAX_ATTACHMENTS_PER_TURN)


class CancelChatRequest(BaseModel):
    turn_id: str | None = Field(default=None, pattern=TURN_ID_PATTERN)


class ChatResponse(BaseModel):
    response: list
    conversation_round: int | None
    saved: bool
    created_at: int | None = None
    attachments: list[dict] | None = None

    @model_serializer
    def _serialize(self) -> dict:
        payload = {
            'response': self.response,
            'conversation_round': self.conversation_round,
            'saved': self.saved,
        }
        if self.created_at is not None:
            payload['created_at'] = self.created_at
        if self.attachments:
            payload['attachments'] = self.attachments
        return payload


router = APIRouter()


def _validate_user_input(request: Request, user_input: str, *, has_attachments: bool = False) -> None:
    limit = request.app.state.system_config.chat.max_message_chars
    if len(user_input) > limit:
        raise ChatNotStarted(422, f'消息最多可输入 {limit} 个字符')
    if not user_input.strip() and not has_attachments:
        raise ChatNotStarted(422, '请输入消息内容')


def _build_response(result: dict) -> ChatResponse:
    message = result.get('message') or {}
    return ChatResponse(
        response=result['ai_response'],
        conversation_round=message.get('conversation_round') if result['saved'] else None,
        saved=result['saved'],
        created_at=message.get('created_at'),
        attachments=message.get('attachments') or None,
    )


def _raise_not_started(error: ChatNotStarted):
    raise HTTPException(
        error.status_code,
        detail={'code': 'CHAT_NOT_STARTED', 'message': str(error)},
    ) from None


def _sse_event(event: str, payload: dict) -> str:
    return f'event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'


def _friendly_stream_error(error: BaseException, *, had_images: bool) -> str:
    if isinstance(error, ChatNotStarted):
        return str(error)
    lower = str(error).lower()
    if had_images and any(tag in lower for tag in ('image_url', 'vision', 'multimodal', 'invalid content', '400')):
        return '当前所选模型接口暂不支持图片输入，请切换到支持视觉的模型后再试'
    return 'AI 处理失败，请稍后重试'


@router.post('/chat', response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    has_media = bool(req.attachment_ids or req.quoted_attachment_ids)
    try:
        _validate_user_input(request, req.user_input, has_attachments=bool(req.attachment_ids))
        kwargs: dict = {}
        if req.turn_id is not None:
            kwargs['turn_id'] = req.turn_id
        if has_media:
            kwargs['attachment_ids'] = req.attachment_ids
            kwargs['quoted_attachment_ids'] = req.quoted_attachment_ids
        result = await request.app.state.chat.send(request.state.user, req.user_input, **kwargs)
    except ChatCancelled:
        raise HTTPException(499, detail={'code': 'CHAT_CANCELLED', 'message': '已停止本次回复'}) from None
    except ChatNotStarted as error:
        _raise_not_started(error)
    except Exception as error:
        logger.error('chat execution failed: %s', type(error).__name__)
        raise HTTPException(500, 'AI 处理失败，请稍后重试') from None

    return _build_response(result)


@router.post('/chat/stream')
async def chat_stream(req: ChatRequest, request: Request):
    has_media = bool(req.attachment_ids or req.quoted_attachment_ids)
    try:
        _validate_user_input(request, req.user_input, has_attachments=bool(req.attachment_ids))
        kwargs: dict = {'turn_id': req.turn_id}
        if has_media:
            kwargs['attachment_ids'] = req.attachment_ids
            kwargs['quoted_attachment_ids'] = req.quoted_attachment_ids
        queue = await request.app.state.chat.start_stream(
            request.state.user, req.user_input, **kwargs,
        )
    except ChatCancelled:
        raise HTTPException(499, detail={'code': 'CHAT_CANCELLED', 'message': '已停止本次回复'}) from None
    except ChatNotStarted as error:
        _raise_not_started(error)
    except Exception as error:
        logger.error('chat stream start failed: %s', type(error).__name__)
        raise HTTPException(500, 'AI 处理失败，请稍后重试') from None

    async def events():
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=STREAM_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # 工具执行等待期间定期发送 SSE 注释保活帧，并在连接已断开时及时通知取消。
                if await request.is_disconnected():
                    request.app.state.chat.cancel(request.state.user.id, req.turn_id)
                    return
                yield ': ping\n\n'
                continue

            if kind == 'bubble':
                yield _sse_event('bubble', {'text': payload})
            elif kind == 'done':
                yield _sse_event('done', _build_response(payload).model_dump(exclude_none=True))
                return
            elif kind == 'cancelled':
                yield _sse_event('cancelled', {'code': 'CHAT_CANCELLED', 'message': '已停止本次回复'})
                return
            elif kind == 'error':
                logger.error('chat stream execution failed: %s', type(payload).__name__)
                yield _sse_event('error', {'message': _friendly_stream_error(payload, had_images=has_media)})
                return

    return StreamingResponse(
        events(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@router.post('/chat/cancel')
async def cancel_chat(req: CancelChatRequest, request: Request):
    cancelled = request.app.state.chat.cancel(request.state.user.id, req.turn_id)
    return {'ok': True, 'cancelled': cancelled}
