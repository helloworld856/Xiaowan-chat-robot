"""固定管理员账户与服务端登录会话，不依赖聊天数据库。"""
import hashlib
import json
import math
import os
import secrets
import tempfile
import time
from pathlib import Path
from threading import RLock
from typing import Callable

COOKIE_NAME = 'xiaowan_session'
SESSION_TTL_SECONDS = 8 * 60 * 60
HASH_ITERATIONS = 600_000


class InvalidCredentials(Exception):
    pass


class InvalidPassword(Exception):
    pass


class PasswordSaveFailed(Exception):
    pass


class LoginThrottled(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, HASH_ITERATIONS)
    return f'pbkdf2_sha256${HASH_ITERATIONS}${salt.hex()}${digest.hex()}'


class AuthService:
    def __init__(self, credentials_path: Path | None = None, clock: Callable[[], float] = time.monotonic):
        path = credentials_path or Path(__file__).with_name('admin.json')
        try:
            credentials = json.loads(path.read_text(encoding='utf-8'))
            if set(credentials) != {'username', 'password_hash'} or credentials['username'] != 'admin':
                raise ValueError('only admin is supported')
            algorithm, iterations, salt, digest = credentials['password_hash'].split('$')
            self.iterations = int(iterations)
            self.salt, self.digest = bytes.fromhex(salt), bytes.fromhex(digest)
            if (algorithm != 'pbkdf2_sha256' or not HASH_ITERATIONS <= self.iterations <= 2_000_000
                    or len(self.salt) != 16 or len(self.digest) != 32):
                raise ValueError('invalid password hash')
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise RuntimeError('管理员账户文件无效，请检查 auth_config/admin.json') from error
        self.clock = clock
        self.credentials_path = path
        self.lock = RLock()
        self.sessions: dict[str, float] = {}
        self.failures: dict[str, list[float]] = {}

    def _prune(self, now: float):
        self.sessions = {token: expires for token, expires in self.sessions.items() if expires > now}
        self.failures = {client: recent for client, attempts in self.failures.items()
                         if (recent := [attempt for attempt in attempts if now - attempt < 60])}

    def login(self, username: str, password: str, client: str) -> str:
        with self.lock:
            now = self.clock()
            self._prune(now)
            attempts = self._get_attempts(client, now)
            digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), self.salt, self.iterations)
            valid_password = secrets.compare_digest(digest, self.digest)
            valid_username = secrets.compare_digest(username.encode('utf-8'), b'admin')
            if not (valid_username and valid_password):
                self.failures[client] = [*attempts, now]
                raise InvalidCredentials()
            self.failures.pop(client, None)
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + SESSION_TTL_SECONDS
            return token

    def _get_attempts(self, client: str, now: float) -> list[float]:
        attempts = self.failures.get(client, [])
        if len(attempts) >= 5:
            raise LoginThrottled(max(1, math.ceil(60 - (now - attempts[0]))))
        return attempts

    def _save_credentials(self, password_hash: str):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.credentials_path.parent,
                                             prefix='.admin-', suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                json.dump({'username': 'admin', 'password_hash': password_hash}, file, indent=2)
                file.write('\n')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.credentials_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def change_password(self, current_password: str, new_password: str, client: str):
        # 与 login 共用锁：先持久化，再替换内存哈希和撤销会话，不产生中间登录窗口。
        with self.lock:
            now = self.clock()
            self._prune(now)
            attempts = self._get_attempts(client, now)
            digest = hashlib.pbkdf2_hmac('sha256', current_password.encode('utf-8'), self.salt, self.iterations)
            if not secrets.compare_digest(digest, self.digest):
                self.failures[client] = [*attempts, now]
                raise InvalidCredentials()
            if not 6 <= len(new_password) <= 128:
                raise InvalidPassword('新密码须为 6–128 个字符')
            if secrets.compare_digest(current_password.encode('utf-8'), new_password.encode('utf-8')):
                raise InvalidPassword('新密码不能与当前密码相同')
            password_hash = hash_password(new_password)
            _, iterations, salt, encoded_digest = password_hash.split('$')
            new_salt, new_digest = bytes.fromhex(salt), bytes.fromhex(encoded_digest)
            try:
                self._save_credentials(password_hash)
            except OSError:
                raise PasswordSaveFailed('保存密码失败，原有密码仍有效') from None
            self.iterations, self.salt, self.digest = int(iterations), new_salt, new_digest
            self.sessions.clear()
            self.failures.pop(client, None)

    def get_user(self, token: str | None) -> str | None:
        if not token or len(token) != 43:
            return None
        with self.lock:
            self._prune(self.clock())
            return 'admin' if hashlib.sha256(token.encode()).hexdigest() in self.sessions else None

    def logout(self, token: str | None):
        if token:
            with self.lock:
                self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
