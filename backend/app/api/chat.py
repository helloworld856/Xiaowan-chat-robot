import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_serializer

from app.account_logging import logger
from app.chat.progress import ChatCancelled
from app.chat.scheduler import ChatNotStarted

TURN_ID_PATTERN = r'^[A-Za-z0-9_-]{1,64}$'


class ChatRequest(BaseModel):
    user_input: str
    turn_id: str | None = Field(default=None, pattern=TURN_ID_PATTERN)


class RegenerateRequest(BaseModel):
    conversation_round: int = Field(ge=0)
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


def _stream_execution(execute):
    loop = asyncio.get_running_loop()
    events: asyncio.Queue[dict] = asyncio.Queue()

    def push(payload: dict) -> None:
        loop.call_soon_threadsafe(events.put_nowait, payload)

    def on_stage(stage: str, label: str) -> None:
        push({'type': 'status', 'stage': stage, 'label': label})

    def on_cancel() -> None:
        push({'type': 'cancelled'})

    async def runner():
        try:
            result = await execute(on_stage, on_cancel)
        except ChatCancelled:
            events.put_nowait({'type': 'cancelled'})
        except ChatNotStarted as error:
            events.put_nowait({
                'type': 'error',
                'status': error.status_code,
                'code': 'CHAT_NOT_STARTED',
                'message': str(error),
            })
        except Exception as error:
            logger.error('chat execution failed: %s', type(error).__name__)
            events.put_nowait({'type': 'error', 'status': 500, 'message': 'AI 处理失败，请稍后重试'})
        else:
            events.put_nowait({'type': 'done', **_build_response(result).model_dump()})

    task = asyncio.create_task(runner())
    task.add_done_callback(lambda done: not done.cancelled() and done.exception())

    async def event_stream():
        while True:
            event = await events.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event['type'] in ('done', 'cancelled', 'error'):
                break

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={'X-Accel-Buffering': 'no'},
    )


@router.post('/chat', response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    try:
        _validate_user_input(request, req.user_input)
        # 调度使用认证后的账号，消息体只提供聊天内容。
        result = await request.app.state.chat.send(request.state.user, req.user_input)
    except ChatNotStarted as error:
        _raise_not_started(error)
    except Exception as error:
        logger.error('chat execution failed: %s', type(error).__name__)
        raise HTTPException(500, 'AI 处理失败，请稍后重试') from None

    return _build_response(result)


@router.post('/chat/stream')
async def chat_stream(req: ChatRequest, request: Request):
    try:
        _validate_user_input(request, req.user_input)
    except ChatNotStarted as error:
        _raise_not_started(error)

    user = request.state.user
    sessions = request.app.state.chat
    return _stream_execution(
        lambda on_stage, on_cancel: sessions.send(
            user, req.user_input, turn_id=req.turn_id, cancellable=True,
            on_stage=on_stage, on_cancel=on_cancel,
        )
    )


@router.post('/chat/regenerate', response_model=ChatResponse)
async def regenerate_chat(req: RegenerateRequest, request: Request):
    try:
        result = await request.app.state.chat.regenerate(
            request.state.user, req.conversation_round, turn_id=req.turn_id,
        )
    except ChatCancelled:
        raise HTTPException(499, '已取消生成') from None
    except ChatNotStarted as error:
        _raise_not_started(error)
    except Exception as error:
        logger.error('chat regenerate failed: %s', type(error).__name__)
        raise HTTPException(500, 'AI 处理失败，请稍后重试') from None

    return _build_response(result)


@router.post('/chat/regenerate/stream')
async def regenerate_chat_stream(req: RegenerateRequest, request: Request):
    user = request.state.user
    sessions = request.app.state.chat
    return _stream_execution(
        lambda on_stage, on_cancel: sessions.regenerate(
            user, req.conversation_round, turn_id=req.turn_id, cancellable=True,
            on_stage=on_stage, on_cancel=on_cancel,
        )
    )


@router.post('/chat/cancel')
async def cancel_chat(req: CancelChatRequest, request: Request):
    cancelled = request.app.state.chat.cancel(request.state.user.id, req.turn_id)
    return {'ok': True, 'cancelled': cancelled}
