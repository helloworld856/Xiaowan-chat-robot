import logging
import os
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import configer
from app.accounts.files import UserDataPaths, protect_file


MAX_LOG_BYTES = 2_000_000
BACKUP_COUNT = 3
MAX_OPEN_ACCOUNT_LOGS = 32


account_log = ContextVar[tuple[UserDataPaths, int] | None]('account_log', default=None)


@contextmanager
def account_log_context(paths: UserDataPaths | None, user_id: int | None):
    # 把账号绑定到当前请求，退出时恢复原上下文，避免后续请求串用。
    token = account_log.set((paths, user_id) if paths is not None and user_id is not None else None)
    try:
        yield
    finally:
        account_log.reset(token)


class AccountLabel(logging.Filter):
    def filter(self, record):
        context = account_log.get()
        record.account_label = f' [账号 {context[1]}]' if context is not None else ''
        return True


class PrivateLogFile(RotatingFileHandler):
    def _open(self):
        protect_file(Path(self.baseFilename))
        return open(self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors,
                    opener=lambda path, flags: os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0), 0o600))

    def handleError(self, record):
        # The router reports the I/O failure without printing the original record.
        raise


class AccountFileHandler(logging.Handler):
    def __init__(self, path: Path, *, max_bytes=MAX_LOG_BYTES, max_open=MAX_OPEN_ACCOUNT_LOGS):
        super().__init__()
        self._options = dict(maxBytes=max_bytes, backupCount=BACKUP_COUNT, delay=True, encoding='utf-8',
                             errors='backslashreplace')
        self._system = RotatingFileHandler(path, **self._options)
        self._personal = OrderedDict()
        self._max_open = max_open

    def setFormatter(self, formatter):
        super().setFormatter(formatter)
        self._system.setFormatter(formatter)

    @staticmethod
    def _close_personal(handler):
        try:
            handler.close()
        except OSError:
            pass

    def emit(self, record):
        context = account_log.get()
        if context is None:
            self._system.handle(record)
            return
        paths, user_id = context
        key = (paths.root, user_id)
        try:
            handler = self._personal.get(key)
            if handler is None:
                path = paths.ensure_log_directory(user_id) / 'app.log'
                handler = PrivateLogFile(path, **self._options)
                handler.setFormatter(self.formatter)
                if len(self._personal) >= self._max_open:
                    _, oldest = self._personal.popitem(last=False)
                    self._close_personal(oldest)
                self._personal[key] = handler
            # 最近用过的句柄留在队尾，超出上限时关闭最久未用的句柄。
            self._personal.move_to_end(key)
            handler.handle(record)
        except (OSError, ValueError) as error:
            failed = self._personal.pop(key, None)
            if failed is not None:
                self._close_personal(failed)
            # 只向系统日志报告账号与异常类型，避免把原日志正文写过去。
            fallback = logging.LogRecord(record.name, logging.WARNING, __file__, 0,
                                         '账号 %d 的运行日志写入失败（%s）', (user_id, type(error).__name__), None)
            fallback.account_label = ''
            self._system.handle(fallback)

    def close_accounts(self, paths: UserDataPaths | None = None) -> None:
        with self.lock:
            for key in list(self._personal):
                if paths is None or key[0] == paths.root:
                    self._close_personal(self._personal.pop(key))

    def close(self):
        self.close_accounts()
        self._system.close()
        super().close()


def get_logger():
    logger = logging.getLogger('xiaowan')
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter('%(asctime)s %(levelname)s%(account_label)s %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
    log_path = Path(configer.log_save_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = AccountFileHandler(log_path)
    handler.setFormatter(formatter)
    handler.addFilter(AccountLabel())
    logger.addHandler(handler)
    return logger


def close_account_logs(paths: UserDataPaths) -> None:
    for handler in logger.handlers:
        if isinstance(handler, AccountFileHandler):
            handler.close_accounts(paths)


logger = get_logger()
