"""Persistent account records and import of the legacy administrator."""

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Callable, Literal, Protocol

import pymysql

from app.config import BACKEND_ROOT
from app.json_files import atomic_write_json

HASH_ITERATIONS = 600_000
LEGACY_ADMIN_PATH = BACKEND_ROOT / 'auth_config' / 'admin.json'
Role = Literal['admin', 'user']


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: Role


@dataclass(frozen=True)
class Account:
    user: User
    password_hash: str = field(repr=False)


class DuplicateUsername(Exception):
    pass


class ReservedUsername(DuplicateUsername):
    pass


class AccountStorageError(Exception):
    pass


class PasswordCommitUnknown(Exception):
    pass


class AccountStore(Protocol):
    def find(self, username_key: str) -> Account | None: ...

    def register(self, username: str, password_hash: str) -> User: ...

    def save_password(self, account: Account, password_hash: str) -> None: ...


def unpack_hash(encoded: str) -> tuple[int, bytes, bytes]:
    algorithm, iterations_text, salt_hex, digest_hex = encoded.split('$')
    iterations = int(iterations_text)
    salt, digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    if (algorithm != 'pbkdf2_sha256' or not HASH_ITERATIONS <= iterations <= 2_000_000
            or len(salt) != 16 or len(digest) != 32):
        raise ValueError('invalid password hash')
    return iterations, salt, digest


def read_legacy_admin(path: Path) -> str:
    try:
        credentials = json.loads(path.read_text(encoding='utf-8'))
        if set(credentials) != {'username', 'password_hash'} or credentials['username'] != 'admin':
            raise ValueError('invalid administrator record')
        password_hash = credentials['password_hash']
        unpack_hash(password_hash)
        return password_hash
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError('管理员旧账户文件无效，请检查 auth_config/admin.json') from None


class SQLAccountStore:
    def __init__(self, pool, *, prepare_user: Callable[[User], None] | None = None):
        self.pool = pool
        self.prepare_user = prepare_user

    def find(self, username_key: str) -> Account | None:
        try:
            with self.pool.connection() as db, db.cursor() as cursor:
                cursor.execute('SELECT id, username, role, password_hash FROM users WHERE username_key = %s',
                               (username_key,))
                row = cursor.fetchone()
        except (OSError, pymysql.MySQLError):
            raise AccountStorageError() from None
        if row is None:
            return None
        user_id, username, role, password_hash = row
        if role not in ('admin', 'user'):
            raise AccountStorageError()
        return Account(User(user_id, username, role), password_hash)

    def list_users(self) -> list[User]:
        with self.pool.connection() as db, db.cursor() as cursor:
            cursor.execute('SELECT id, username, role FROM users ORDER BY id')
            return [User(*row) for row in cursor.fetchall()]

    def _insert(self, username: str, password_hash: str, role: Role) -> User:
        try:
            with self.pool.connection() as db, db.cursor() as cursor:
                cursor.execute('INSERT INTO users (username, username_key, password_hash, role) VALUES (%s, %s, %s, %s)',
                               (username, username.lower(), password_hash, role))
                user = User(cursor.lastrowid, username, role)
                if role == 'user' and self.prepare_user is not None:
                    self.prepare_user(user)
                db.commit()
        except pymysql.IntegrityError as error:
            if error.args and error.args[0] == 1062:
                raise DuplicateUsername() from None
            raise AccountStorageError() from None
        except (OSError, pymysql.MySQLError):
            raise AccountStorageError() from None
        return user

    def register(self, username: str, password_hash: str) -> User:
        return self._insert(username, password_hash, 'user')

    def save_password(self, account: Account, password_hash: str) -> None:
        commit_attempted = False
        try:
            with self.pool.connection() as db, db.cursor() as cursor:
                cursor.execute('UPDATE users SET password_hash = %s WHERE id = %s AND password_hash = %s',
                               (password_hash, account.user.id, account.password_hash))
                if cursor.rowcount != 1:
                    raise AccountStorageError()
                commit_attempted = True
                db.commit()
        except Exception as error:
            if commit_attempted:
                # MySQL may have committed before the connection failed to acknowledge it.
                raise PasswordCommitUnknown() from None
            if isinstance(error, (OSError, pymysql.MySQLError)):
                raise AccountStorageError() from None
            raise

    def seed_admin(self, credentials_path: Path | None = None) -> User:
        """Import once. Existing account details and any changed password stay authoritative."""
        existing = self.find('admin')
        if existing is None:
            password_hash = read_legacy_admin(credentials_path or LEGACY_ADMIN_PATH)
            try:
                return self._insert('admin', password_hash, 'admin')
            except DuplicateUsername:
                existing = self.find('admin')
        if existing is None or existing.user.role != 'admin':
            raise RuntimeError('用户名 admin 已被普通账号占用，不能迁移管理员')
        return existing.user


class LocalAccountStore:
    """Isolated, no-MySQL fixture; an explicit legacy file can be used to test import and password changes."""

    def __init__(self, credentials_path: Path | None = None):
        self.credentials_path = credentials_path
        self.lock = RLock()
        self.accounts = {'admin': Account(User(1, 'admin', 'admin'), read_legacy_admin(credentials_path or LEGACY_ADMIN_PATH))}

    def find(self, username_key: str) -> Account | None:
        with self.lock:
            return self.accounts.get(username_key)

    def register(self, username: str, password_hash: str) -> User:
        with self.lock:
            key = username.lower()
            if key in self.accounts:
                raise DuplicateUsername()
            user = User(len(self.accounts) + 1, username, 'user')
            self.accounts[key] = Account(user, password_hash)
            return user

    def save_password(self, account: Account, password_hash: str) -> None:
        with self.lock:
            key = account.user.username.lower()
            if self.accounts.get(key) != account:
                raise AccountStorageError()
            if key == 'admin' and self.credentials_path is not None:
                self._write_legacy_password(password_hash)
            self.accounts[key] = replace(account, password_hash=password_hash)

    def _write_legacy_password(self, password_hash: str) -> None:
        try:
            atomic_write_json(self.credentials_path, {'username': 'admin', 'password_hash': password_hash})
        except OSError:
            raise AccountStorageError() from None
