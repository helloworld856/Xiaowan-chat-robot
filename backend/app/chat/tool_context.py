"""Account-scoped execution context, per-turn quotas, taint tracking, and two-phase write confirmation for tools."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from secrets import token_hex
from threading import Lock
from time import monotonic
from typing import Any, Literal

ToolRiskLevel = Literal['read', 'write_safe', 'write_destructive']

CONFIRMATION_TTL_SECONDS = 300.0
MAX_PENDING_CONFIRMATIONS = 128


@dataclass(frozen=True)
class PendingConfirmation:
    token: str
    user_id: int
    tool_name: str
    summary: str
    arguments: dict[str, Any]
    expires_at: float


class PendingWriteConfirmations:
    """进程内两阶段破坏性写操作确认池，严格绑定账号 ID 与参数摘要并自动过期清理。"""

    def __init__(self, *, ttl_seconds: float = CONFIRMATION_TTL_SECONDS, max_items: int = MAX_PENDING_CONFIRMATIONS):
        self._ttl = ttl_seconds
        self._max_items = max_items
        self._lock = Lock()
        self._items: dict[str, PendingConfirmation] = {}

    def _prune_unlocked(self, now: float) -> None:
        expired = [key for key, item in self._items.items() if item.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
        while len(self._items) >= self._max_items:
            oldest_key = next(iter(self._items))
            self._items.pop(oldest_key, None)

    def stage(self, user_id: int, tool_name: str, summary: str, arguments: dict[str, Any]) -> PendingConfirmation:
        now = monotonic()
        clean_args = {k: v for k, v in arguments.items() if k != 'confirm_token'}
        with self._lock:
            self._prune_unlocked(now)
            token = f'cfm_{token_hex(6)}'
            entry = PendingConfirmation(
                token=token,
                user_id=user_id,
                tool_name=tool_name,
                summary=summary,
                arguments=clean_args,
                expires_at=now + self._ttl,
            )
            self._items[token] = entry
            return entry

    def consume(self, user_id: int, tool_name: str, token: str,
                arguments: dict[str, Any] | None = None) -> PendingConfirmation | None:
        if not token:
            return None
        now = monotonic()
        with self._lock:
            self._prune_unlocked(now)
            entry = self._items.get(token)
            if entry is None:
                return None
            if entry.user_id != user_id or entry.tool_name != tool_name:
                return None
            if arguments is not None:
                clean_args = {k: v for k, v in arguments.items() if k != 'confirm_token'}
                if clean_args != entry.arguments:
                    return None
            self._items.pop(token, None)
            return entry

    def clear_user(self, user_id: int) -> None:
        with self._lock:
            keys = [key for key, item in self._items.items() if item.user_id == user_id]
            for key in keys:
                self._items.pop(key, None)


DEFAULT_CONFIRMATIONS = PendingWriteConfirmations()


@dataclass
class ChatToolContext:
    user_id: int
    connection: Callable[[], Any] | None = None
    window_start_round: int | None = None
    search_config: Any = None
    max_tool_calls: int = 4
    max_web_searches: int = 2
    max_safe_writes: int = 2
    tool_calls: int = 0
    web_searches: int = 0
    safe_writes: int = 0
    external_tainted: bool = False
    confirmations: PendingWriteConfirmations = field(default_factory=lambda: DEFAULT_CONFIRMATIONS)


_CURRENT_TOOL_CONTEXT: ContextVar[ChatToolContext | None] = ContextVar('xiaowan_chat_tool_context', default=None)


def current_tool_context() -> ChatToolContext | None:
    return _CURRENT_TOOL_CONTEXT.get()


@contextmanager
def chat_tool_context(context: ChatToolContext | None):
    token = _CURRENT_TOOL_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_TOOL_CONTEXT.reset(token)
