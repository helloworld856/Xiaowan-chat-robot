"""The authenticated account attached to the current execution context."""

from contextlib import contextmanager
from contextvars import ContextVar

from user_data_config import UserDataPaths


account_log = ContextVar[tuple[UserDataPaths, int] | None]('account_log', default=None)


@contextmanager
def account_log_context(paths: UserDataPaths | None, user_id: int | None):
    token = account_log.set((paths, user_id) if paths is not None and user_id is not None else None)
    try:
        yield
    finally:
        account_log.reset(token)
