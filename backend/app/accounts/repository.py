"""Account storage and one-time administrator creation."""

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Callable, Literal, Protocol

import pymysql

from app.json_files import atomic_write_json

HASH_ITERATIONS = 600_000
USERNAME_PATTERN = r'^[a-zA-Z0-9_]{3,32}$'
Role = Literal['admin', 'user']


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: Role
    uses_username_directory: bool = False


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


class AccountDeleteUnknown(Exception):
    pass


class AccountStore(Protocol):
    def find(self, username_key: str) -> Account | None: ...

    def register(self, username: str, password_hash: str) -> User: ...

    def save_password(self, account: Account, password_hash: str) -> None: ...

    def delete(self, account: Account) -> None: ...


def unpack_hash(encoded: str) -> tuple[int, bytes, bytes]:
    algorithm, iterations_text, salt_hex, digest_hex = encoded.split('$')
    iterations = int(iterations_text)
    salt, digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    if (algorithm != 'pbkdf2_sha256' or not HASH_ITERATIONS <= iterations <= 2_000_000
            or len(salt) != 16 or len(digest) != 32):
        raise ValueError('invalid password hash')
    return iterations, salt, digest


def _read_admin_credentials(path: Path) -> str:
    try:
        credentials = json.loads(path.read_text(encoding='utf-8'))
        if set(credentials) != {'username', 'password_hash'} or credentials['username'] != 'admin':
            raise ValueError('invalid administrator record')
        password_hash = credentials['password_hash']
        unpack_hash(password_hash)
        return password_hash
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError('管理员凭据文件无效') from None


class SQLAccountStore:
    def __init__(self, pool, *, prepare_user: Callable[[User], None] | None = None):
        self.pool = pool
        self.prepare_user = prepare_user

    def find(self, username_key: str) -> Account | None:
        try:
            with self.pool.connection() as db, db.cursor() as cursor:
                cursor.execute('''SELECT id, username, role, password_hash, uses_username_directory
                                  FROM users WHERE username_key = %s''', (username_key,))
                row = cursor.fetchone()
        except (OSError, pymysql.MySQLError):
            raise AccountStorageError() from None
        if row is None:
            return None
        user_id, username, role, password_hash, uses_username_directory = row
        if role not in ('admin', 'user'):
            raise AccountStorageError()
        return Account(User(user_id, username, role, bool(uses_username_directory)), password_hash)

    def _insert(self, username: str, password_hash: str, role: Role) -> User:
        uses_username_directory = role == 'user'
        try:
            with self.pool.connection() as db, db.cursor() as cursor:
                cursor.execute('''INSERT INTO users (username, username_key, password_hash, role, uses_username_directory)
                                  VALUES (%s, %s, %s, %s, %s)''',
                               (username, username.lower(), password_hash, role, uses_username_directory))
                user = User(cursor.lastrowid, username, role, uses_username_directory)
                # 账号提交前先准备个人目录，初始化失败时数据库连接池会回滚本次插入。
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

    def find_user_by_id(self, user_id: int) -> User | None:
        with self.pool.connection() as db, db.cursor() as cursor:
            cursor.execute('SELECT id, username, role, uses_username_directory FROM users WHERE id = %s', (user_id,))
            row = cursor.fetchone()
        return User(row[0], row[1], row[2], bool(row[3])) if row is not None else None

    def delete(self, account: Account) -> None:
        from app.database.mysql import delete_user_history

        user_id = account.user.id
        commit_attempted = False
        try:
            with self.pool.connection() as db, db.cursor() as cursor:
                cursor.execute("DELETE FROM users WHERE id = %s AND role = 'user' AND password_hash = %s",
                               (user_id, account.password_hash))
                if cursor.rowcount != 1:
                    raise AccountStorageError()
                delete_user_history(cursor, user_id)
                commit_attempted = True
                db.commit()
        except (OSError, pymysql.MySQLError):
            if commit_attempted:
                raise AccountDeleteUnknown() from None
            raise AccountStorageError() from None

    def users_with_id_directories(self) -> list[User]:
        with self.pool.connection() as db, db.cursor() as cursor:
            cursor.execute('SELECT id, username, role FROM users WHERE uses_username_directory = FALSE')
            return [User(*row) for row in cursor.fetchall()]

    def use_username_directory(self, user_id: int) -> None:
        with self.pool.connection() as db, db.cursor() as cursor:
            cursor.execute('UPDATE users SET uses_username_directory = TRUE WHERE id = %s', (user_id,))
            db.commit()

    def save_password(self, account: Account, password_hash: str) -> None:
        # 区分提交前失败与提交结果未知，认证层据此决定是否撤销会话。
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

    def get_admin(self) -> User | None:
        existing = self.find('admin')
        if existing is None:
            return None
        if existing.user.role != 'admin':
            raise RuntimeError('用户名 admin 已被普通账号占用，无法初始化管理员')
        return existing.user

    def initialize_admin(self, password_hash: str) -> User:
        existing = self.get_admin()
        if existing is not None:
            return existing
        unpack_hash(password_hash)
        try:
            return self._insert('admin', password_hash, 'admin')
        except DuplicateUsername:
            # 并发初始化只允许一次插入，晚到的命令不会覆盖密码。
            existing = self.get_admin()
            if existing is None:
                raise AccountStorageError() from None
            return existing


class LocalAccountStore:
    """Isolated store for tests, with credentials supplied explicitly by the caller."""

    def __init__(self, credentials_path: Path | None = None, *, admin_password_hash: str | None = None):
        self.credentials_path = credentials_path
        self.lock = RLock()
        self.accounts: dict[str, Account] = {}
        if credentials_path is not None:
            admin_password_hash = _read_admin_credentials(credentials_path)
        if admin_password_hash is not None:
            self.accounts['admin'] = Account(User(1, 'admin', 'admin'), admin_password_hash)
        self.next_id = len(self.accounts) + 1

    def find(self, username_key: str) -> Account | None:
        with self.lock:
            return self.accounts.get(username_key)

    def register(self, username: str, password_hash: str) -> User:
        with self.lock:
            key = username.lower()
            if key in self.accounts:
                raise DuplicateUsername()
            user = User(self.next_id, username, 'user')
            self.next_id += 1
            self.accounts[key] = Account(user, password_hash)
            return user

    def save_password(self, account: Account, password_hash: str) -> None:
        with self.lock:
            key = account.user.username.lower()
            if self.accounts.get(key) != account:
                raise AccountStorageError()
            if key == 'admin' and self.credentials_path is not None:
                self._write_password(password_hash)
            self.accounts[key] = replace(account, password_hash=password_hash)

    def delete(self, account: Account) -> None:
        with self.lock:
            key = account.user.username.lower()
            if account.user.role != 'user' or self.accounts.get(key) != account:
                raise AccountStorageError()
            del self.accounts[key]

    def _write_password(self, password_hash: str) -> None:
        try:
            atomic_write_json(self.credentials_path, {'username': 'admin', 'password_hash': password_hash})
        except OSError:
            raise AccountStorageError() from None
