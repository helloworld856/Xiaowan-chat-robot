import unittest
from unittest.mock import Mock

from _imports import BACKEND_ROOT as _BACKEND_ROOT


class ConnectionPoolTests(unittest.TestCase):
    def test_checkout_rejects_connection_opened_during_close(self):
        import pymysql
        from database_config import ConnectionPool

        connection = Mock()

        def connect():
            pool.close()
            return connection

        pool = ConnectionPool(connect, max_connections=1)
        with self.assertRaises(pymysql.OperationalError):
            with pool.connection():
                self.fail('connection checked out after pool closed')
        connection.close.assert_called_once()

    def test_reuse_resets_transaction_and_never_shares_an_active_connection(self):
        from database_config import ConnectionPool

        first, second = Mock(), Mock()
        pool = ConnectionPool(Mock(side_effect=[first, second]), max_connections=2)
        self.addCleanup(pool.close)
        with pool.connection() as a:
            with pool.connection() as b:
                self.assertIs(a, first)
                self.assertIs(b, second)
            second.rollback.assert_called_once()
            first.rollback.assert_not_called()
        with pool.connection() as reused:
            self.assertIs(reused, first)
            first.ping.assert_called_once_with(reconnect=False)

    def test_exception_resets_transaction_and_broken_connection_is_discarded(self):
        from database_config import ConnectionPool

        broken, replacement = Mock(), Mock()
        broken.rollback.side_effect = OSError('lost connection')
        pool = ConnectionPool(Mock(side_effect=[broken, replacement]), max_connections=1)
        self.addCleanup(pool.close)
        with self.assertRaisesRegex(ValueError, 'operation failed'):
            with pool.connection():
                raise ValueError('operation failed')
        broken.close.assert_called_once()
        with pool.connection() as connection:
            self.assertIs(connection, replacement)

    def test_failed_checkout_ping_discards_connection_even_if_rollback_works(self):
        from database_config import ConnectionPool

        failed, replacement = Mock(), Mock()
        failed.ping.side_effect = OSError('failed health check')
        pool = ConnectionPool(Mock(side_effect=[failed, replacement]), max_connections=1)
        self.addCleanup(pool.close)
        with pool.connection():
            pass
        with pool.connection() as connection:
            self.assertIs(connection, replacement)
