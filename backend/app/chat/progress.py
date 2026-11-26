"""Cooperative cancellation context for in-flight chat turns."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event


class ChatCancelled(Exception):
    """Raised when the caller explicitly cancels an active or queued chat turn."""


_cancel_event: ContextVar[Event | None] = ContextVar('chat_cancel_event', default=None)


def check_cancelled() -> None:
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise ChatCancelled()


@contextmanager
def chat_cancel_context(cancel_event: Event | None = None):
    token = _cancel_event.set(cancel_event)
    try:
        yield
    finally:
        _cancel_event.reset(token)
