"""Account authentication and process-local cookie sessions."""

import hashlib
import math
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable

from .accounts import (HASH_ITERATIONS, AccountStorageError, AccountStore, DuplicateUsername,
                       LocalAccountStore, PasswordCommitUnknown, SQLAccountStore, User, unpack_hash)

COOKIE_NAME = 'xiaowan_session'
SESSION_TTL_SECONDS = 8 * 60 * 60
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 15
USERNAME_PATTERN = r'^[a-zA-Z0-9_]{3,32}$'
TOKEN_PATTERN = re.compile(r'[a-zA-Z0-9_-]{43}')


class InvalidCredentials(Exception):
    pass


class InvalidSession(Exception):
    pass


class InvalidPassword(Exception):
    pass


class PasswordSaveFailed(Exception):
    pass


class LoginThrottled(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


@dataclass(eq=False, frozen=True)
class _Attempt:
    at: float


class _AttemptLimiter:
    WINDOW_SECONDS = 60
    MAX_CLIENTS = 4096

    def __init__(self, clock: Callable[[], float], limit: int):
        self.clock = clock
        self.limit = limit
        self.lock = Lock()
        self.clients: dict[str, list[_Attempt]] = {}

    def reserve(self, client: str) -> _Attempt:
        with self.lock:
            now = self.clock()
            if client not in self.clients and len(self.clients) >= self.MAX_CLIENTS:
                self.clients = {key: active for key, attempts in self.clients.items()
                                if (active := [a for a in attempts if now - a.at < self.WINDOW_SECONDS])}
                if len(self.clients) >= self.MAX_CLIENTS:
                    oldest = min(attempts[0].at for attempts in self.clients.values())
                    raise LoginThrottled(max(1, math.ceil(self.WINDOW_SECONDS - (now - oldest))))
            active = [a for a in self.clients.get(client, ()) if now - a.at < self.WINDOW_SECONDS]
            if len(active) >= self.limit:
                raise LoginThrottled(max(1, math.ceil(self.WINDOW_SECONDS - (now - active[0].at))))
            attempt = _Attempt(now)
            self.clients[client] = [*active, attempt]
            return attempt

    def release(self, client: str, attempt: _Attempt) -> None:
        with self.lock:
            active = [a for a in self.clients.get(client, ()) if a is not attempt]
            if active:
                self.clients[client] = active
            else:
                self.clients.pop(client, None)


@dataclass(frozen=True)
class _Session:
    user: User
    expires: float


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, HASH_ITERATIONS)
    return f'pbkdf2_sha256${HASH_ITERATIONS}${salt.hex()}${digest.hex()}'


def _verify_password(password: str, password_hash: str | None) -> bool:
    # Also hash unknown usernames to avoid a cheap account-existence timing shortcut.
    iterations, salt, expected = HASH_ITERATIONS, b'\x00' * 16, b'\x00' * 32
    if password_hash is not None:
        try:
            iterations, salt, expected = unpack_hash(password_hash)
        except (ValueError, AttributeError):
            raise AccountStorageError() from None
    try:
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    except UnicodeError:
        return False
    return secrets.compare_digest(digest, expected) and password_hash is not None


def _validate_new_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise InvalidPassword(f'新密码须为 {MIN_PASSWORD_LENGTH}–{MAX_PASSWORD_LENGTH} 个字符')
    try:
        password.encode('utf-8')
    except UnicodeError:
        raise InvalidPassword('密码包含无效字符') from None


def _token_digest(token: str | None) -> str | None:
    if not token or not TOKEN_PATTERN.fullmatch(token):
        return None
    return hashlib.sha256(token.encode('ascii')).hexdigest()


class AuthService:
    def __init__(self, credentials_path: Path | None = None, clock: Callable[[], float] = time.monotonic,
                 *, account_store: AccountStore | None = None):
        self.account_store = account_store if account_store is not None else LocalAccountStore(credentials_path)
        self.clock = clock
        self._session_lock = Lock()
        self._sessions: dict[str, _Session] = {}
        # A fixed-size set of locks avoids retaining one lock for every supplied username.
        self._account_locks = tuple(Lock() for _ in range(64))
        self._login_attempts = _AttemptLimiter(clock, limit=5)
        self._registrations = _AttemptLimiter(clock, limit=5)

    def _account_lock(self, username_key: str):
        return self._account_locks[hash(username_key) % len(self._account_locks)]

    def _prune_sessions(self, now: float) -> None:
        while self._sessions:
            oldest = next(iter(self._sessions))
            if self._sessions[oldest].expires > now:
                break
            del self._sessions[oldest]

    def _revoke_user_sessions(self, user_id: int) -> None:
        with self._session_lock:
            self._sessions = {key: session for key, session in self._sessions.items() if session.user.id != user_id}

    def register(self, username: str, password: str, client: str) -> User:
        if re.fullmatch(USERNAME_PATTERN, username) is None:
            raise ValueError('用户名须为 3–32 位字母、数字或下划线')
        _validate_new_password(password)
        attempt = self._registrations.reserve(client)
        try:
            return self.account_store.register(username, hash_password(password))
        except AccountStorageError:
            self._registrations.release(client, attempt)
            raise

    def login(self, username: str, password: str, client: str, *, previous_token: str | None = None) -> tuple[str, User]:
        username = username.strip()
        username_key = username.lower()
        attempt = self._login_attempts.reserve(client)
        try:
            with self._account_lock(username_key):
                account = self.account_store.find(username_key) if re.fullmatch(USERNAME_PATTERN, username) else None
                valid = _verify_password(password, account.password_hash if account else None)
                if account is None or not valid:
                    raise InvalidCredentials()
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode('ascii')).hexdigest()
                previous_digest = _token_digest(previous_token)
                with self._session_lock:
                    now = self.clock()
                    self._prune_sessions(now)
                    if previous_digest is not None:
                        self._sessions.pop(previous_digest, None)
                    self._sessions[token_hash] = _Session(account.user, now + SESSION_TTL_SECONDS)
        except AccountStorageError:
            self._login_attempts.release(client, attempt)
            raise
        self._login_attempts.release(client, attempt)
        return token, account.user

    def change_password(self, user: User, token: str | None, current_password: str, new_password: str, client: str):
        _validate_new_password(new_password)
        username_key = user.username.lower()
        attempt = self._login_attempts.reserve(client)
        try:
            with self._account_lock(username_key):
                if self.get_user(token) != user:
                    raise InvalidSession()
                account = self.account_store.find(username_key)
                if account is None or account.user != user or not _verify_password(current_password, account.password_hash):
                    raise InvalidCredentials()
                if secrets.compare_digest(current_password.encode('utf-8'), new_password.encode('utf-8')):
                    raise InvalidPassword('新密码不能与当前密码相同')
                try:
                    self.account_store.save_password(account, hash_password(new_password))
                except PasswordCommitUnknown:
                    self._revoke_user_sessions(user.id)
                    raise
                except (AccountStorageError, OSError):
                    raise PasswordSaveFailed() from None
                self._revoke_user_sessions(user.id)
        except (AccountStorageError, InvalidSession, InvalidPassword, PasswordCommitUnknown, PasswordSaveFailed):
            self._login_attempts.release(client, attempt)
            raise
        self._login_attempts.release(client, attempt)

    def get_user(self, token: str | None) -> User | None:
        digest = _token_digest(token)
        if digest is None:
            return None
        with self._session_lock:
            self._prune_sessions(self.clock())
            session = self._sessions.get(digest)
            return session.user if session is not None else None

    def logout(self, token: str | None):
        digest = _token_digest(token)
        if digest is not None:
            with self._session_lock:
                self._sessions.pop(digest, None)
