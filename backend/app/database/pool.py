from contextlib import contextmanager
from logging import WARNING
from threading import BoundedSemaphore, Lock

import pymysql

from app.account_logging import log_event


def _log_pool_error(event: str, error: Exception) -> None:
    code = error.args[0] if isinstance(error, pymysql.MySQLError) and error.args else None
    log_event(event, level=WARNING, system=True, error=type(error).__name__,
              mysql_code=code if type(code) is int else None)


class ConnectionPool:
    """每个调用独占连接，未提交的事务在归还时回滚。"""

    def __init__(self, connect, max_connections=12, wait_timeout=5):
        self._connect = connect
        self._slots = BoundedSemaphore(max_connections)
        self._wait_timeout = wait_timeout
        self._lock = Lock()
        self._idle = []
        self._closed = False

    @staticmethod
    def _close_connection(connection):
        try:
            connection.close()
        except (OSError, pymysql.MySQLError) as error:
            _log_pool_error('db_connection_close_failed', error)

    @contextmanager
    def connection(self):
        if not self._slots.acquire(timeout=self._wait_timeout):
            log_event('db_checkout_timeout', level=WARNING, system=True, timeout_seconds=self._wait_timeout)
            raise pymysql.OperationalError('等待数据库连接超时')
        connection = None
        try:
            with self._lock:
                if self._closed:
                    raise pymysql.OperationalError('数据库连接池已关闭')
                if self._idle:
                    connection = self._idle.pop()
            if connection is not None:
                try:
                    # 复用前检查连接，断开的连接直接淘汰，避免偷偷换掉连接状态。
                    connection.ping(reconnect=False)
                except BaseException as error:
                    failed, connection = connection, None
                    self._close_connection(failed)
                    if not isinstance(error, (OSError, pymysql.MySQLError)):
                        raise
                    _log_pool_error('db_connection_discarded', error)
            if connection is None:
                try:
                    connection = self._connect()
                except (OSError, pymysql.MySQLError) as error:
                    _log_pool_error('db_connection_open_failed', error)
                    raise
            with self._lock:
                if self._closed:
                    raise pymysql.OperationalError('数据库连接池已关闭')
            yield connection
        finally:
            try:
                if connection is not None:
                    try:
                        # 归还前清掉调用方留下的事务，避免下一次借用读到旧状态。
                        connection.rollback()
                    except (OSError, pymysql.MySQLError) as error:
                        _log_pool_error('db_rollback_failed', error)
                        self._close_connection(connection)
                    else:
                        with self._lock:
                            closed = self._closed
                            if not closed:
                                self._idle.append(connection)
                        if closed:
                            self._close_connection(connection)
            finally:
                self._slots.release()

    def close(self):
        with self._lock:
            self._closed = True
            connections, self._idle = self._idle, []
        for connection in connections:
            self._close_connection(connection)
