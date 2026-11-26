"""Per-turn progress stages and cooperative cancellation context."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event


class ChatCancelled(Exception):
    """Raised when the caller explicitly cancels an active or queued chat turn."""


STAGE_LABELS = {
    'queued': '正在排队等待…',
    'analyzing': '正在理解你的话…',
    'generating': '正在组织回复…',
    'tool:get_weather': '正在查询天气…',
    'tool:get_current_time': '正在查看时间…',
}

_status_sink: ContextVar[Callable[[str, str], None] | None] = ContextVar('chat_status_sink', default=None)
_cancel_event: ContextVar[Event | None] = ContextVar('chat_cancel_event', default=None)


def check_cancelled() -> None:
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise ChatCancelled()


def report_stage(stage: str, label: str | None = None) -> None:
    check_cancelled()
    sink = _status_sink.get()
    if sink is not None:
        sink(stage, label or STAGE_LABELS.get(stage, '正在思考…'))


@contextmanager
def chat_progress_context(cancel_event: Event | None = None,
                          on_stage: Callable[[str, str], None] | None = None):
    cancel_token = _cancel_event.set(cancel_event)
    sink_token = _status_sink.set(on_stage)
    try:
        yield
    finally:
        _status_sink.reset(sink_token)
        _cancel_event.reset(cancel_token)
