import logging
import os
import re
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app import config
from app.accounts.files import DIRECTORY_MIGRATION_MARKER, UserDataPaths, protect_file, require_directory, require_file
from app.accounts.repository import USERNAME_PATTERN
from app.system_config import LoggingConfig


_DEFAULTS = LoggingConfig()


def _truncate_existing_log(path: Path) -> None:
    try:
        require_file(path)
        os.close(os.open(path, os.O_WRONLY | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)))
    except FileNotFoundError:
        pass


def reset_active_logs(system_log: Path, paths: UserDataPaths) -> None:
    """清空本轮启动要使用的日志文件，保留轮转归档。"""
    try:
        _truncate_existing_log(system_log)
    except OSError as error:
        logger.warning('无法清空系统日志（%s）', type(error).__name__)

    try:
        require_directory(paths.root)
        entries = list(paths.root.iterdir())
    except FileNotFoundError:
        return
    except OSError as error:
        logger.warning('无法扫描账号日志目录（%s）', type(error).__name__)
        return

    for entry in entries:
        name = entry.name
        legacy = name.isascii() and name.isdecimal() and not name.startswith('0')
        if not legacy and re.fullmatch(USERNAME_PATTERN, name.removeprefix('~')) is None:
            continue
        try:
            require_directory(entry)
            if not legacy:
                try:
                    require_file(entry / 'profile.json')
                except FileNotFoundError:
                    require_file(entry / DIRECTORY_MIGRATION_MARKER)
            _truncate_existing_log(entry / 'logs' / 'app.log')
        except FileNotFoundError:
            continue
        except OSError as error:
            logger.warning('无法清空账号 %s 的日志（%s）', name, type(error).__name__)


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
    def __init__(self, path: Path, *, max_bytes=_DEFAULTS.max_bytes, max_open=_DEFAULTS.max_open_account_logs):
        super().__init__()
        self._options = dict(maxBytes=max_bytes, backupCount=_DEFAULTS.backup_count, delay=True, encoding='utf-8',
                             errors='backslashreplace')
        self._system = RotatingFileHandler(path, **self._options)
        self._personal = OrderedDict()
        self._max_open = max_open

    def configure(self, settings: LoggingConfig) -> None:
        with self.lock:
            self.close_accounts()
            self._options.update(maxBytes=settings.max_bytes, backupCount=settings.backup_count)
            self._system.maxBytes = settings.max_bytes
            self._system.backupCount = settings.backup_count
            self._max_open = settings.max_open_account_logs

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
    log_path = config.SYSTEM_LOG_PATH
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


def configure_logging(settings: LoggingConfig) -> None:
    for handler in logger.handlers:
        if isinstance(handler, AccountFileHandler):
            handler.configure(settings)


logger = get_logger()
