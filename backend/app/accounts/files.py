"""Paths, permissions, and atomic writes for account-owned files."""

import json
import os
import re
import stat
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Literal

from app.accounts.repository import USERNAME_PATTERN, User
from app.config import BACKEND_ROOT
from app.json_files import atomic_write_json


FileName = Literal['profile.json', 'model.json', 'preferences.json']
WINDOWS_DEVICE_NAME = re.compile(r'CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]', re.IGNORECASE)
DIRECTORY_MIGRATION_MARKER = '.legacy-account.json'


def username_directory_name(username: str) -> str:
    if re.fullmatch(USERNAME_PATTERN, username) is None:
        raise ValueError('个人目录的用户名无效')
    # 纯数字名与旧账号 ID 分开，Windows 设备名也统一转义，便于跨平台迁移。
    if username.isdecimal() or WINDOWS_DEVICE_NAME.fullmatch(username):
        return f'~{username}'
    return username


def require_directory(path: Path) -> None:
    details = path.lstat()
    if (not stat.S_ISDIR(details.st_mode)
            or getattr(details, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise OSError(f'账号数据路径不是目录: {path.name}')


# 使用 lstat 检查路径自身，拒绝符号链接或非普通文件。
def require_file(path: Path) -> None:
    require_directory(path.parent)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise OSError(f'账号数据路径不是普通文件: {path.name}')


def _restrict(path: Path, mode: int) -> None:
    if os.name == 'posix' and stat.S_IMODE(path.lstat().st_mode) != mode:
        path.chmod(mode)


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_directory(path)
    _restrict(path, 0o700)


def protect_file(path: Path) -> bool:
    """Restrict an existing regular file and report whether it exists."""
    try:
        require_file(path)
    except FileNotFoundError:
        return False
    _restrict(path, 0o600)
    return True


def read_text(path: Path) -> str:
    require_file(path)
    return path.read_text(encoding='utf-8')


def replace_json(path: Path, value) -> None:
    require_directory(path.parent)
    atomic_write_json(path, value)


def _matches_migration_marker(directory: Path, owner: dict) -> bool:
    try:
        return json.loads(read_text(directory / DIRECTORY_MIGRATION_MARKER)) == owner
    except (OSError, ValueError):
        return False


def _sync_directory(path: Path) -> None:
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class UserDataPaths:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else BACKEND_ROOT / 'user_data'
        self._directories: dict[int, Path] = {}
        # 用固定数量的可重入锁串行化同账号写入，存储方法可在锁内调用彼此。
        self._locks = tuple(RLock() for _ in range(64))

    def bind(self, user: User) -> None:
        if not user.uses_username_directory:
            return
        directory = self.root / username_directory_name(user.username)
        with self.lock(user.id):
            self._directories[user.id] = directory

    def unbind(self, user_id: int) -> None:
        with self.lock(user_id):
            self._directories.pop(user_id, None)

    def directory(self, user_id: int) -> Path:
        if type(user_id) is not int or user_id < 1:
            raise ValueError('账号 ID 无效')
        return self._directories.get(user_id) or self.root / str(user_id)

    def file(self, user_id: int, filename: FileName) -> Path:
        return self.directory(user_id) / filename

    def migrate_legacy_directory(self, user: User) -> bool:
        """Move an ID directory at startup, or report an occupied destination."""
        source = self.directory(user.id)
        target = self.root / username_directory_name(user.username)
        owner = {'id': user.id, 'username': user.username}
        try:
            require_directory(self.root)
        except FileNotFoundError:
            return True

        try:
            require_directory(source)
        except FileNotFoundError:
            source_exists = False
        else:
            source_exists = True

        try:
            target.lstat()
        except FileNotFoundError:
            pass
        else:
            # 目录改名后数据库尚未提交时，只接续带有该账号标记的目标目录。
            if source_exists or not _matches_migration_marker(target, owner):
                return False
            _sync_directory(self.root)
            return True

        if source_exists:
            try:
                atomic_write_json(source / DIRECTORY_MIGRATION_MARKER, owner, replace=False)
            except FileExistsError:
                if not _matches_migration_marker(source, owner):
                    raise OSError(f'账号 {user.id} 的目录迁移标记与数据库不一致') from None
            _sync_directory(source)
            source.rename(target)
            _sync_directory(self.root)
        return True

    def lock(self, user_id: int):
        self.directory(user_id)
        return self._locks[user_id % len(self._locks)]

    def ensure_directory(self, user_id: int) -> Path:
        directory = self.directory(user_id)
        _private_directory(self.root)
        _private_directory(directory)
        return directory

    def create_account_directory(self, user: User) -> User:
        with self.lock(user.id):
            _private_directory(self.root)
            if user.uses_username_directory:
                named = self.root / username_directory_name(user.username)
                try:
                    named.mkdir(mode=0o700)
                except FileExistsError:
                    user = replace(user, uses_username_directory=False)
                else:
                    self.bind(user)
                    return user
            (self.root / str(user.id)).mkdir(mode=0o700)
            self.unbind(user.id)
            return user

    def log_directory(self, user_id: int) -> Path:
        return self.directory(user_id) / 'logs'

    def ensure_log_directory(self, user_id: int) -> Path:
        path = self.ensure_directory(user_id) / 'logs'
        _private_directory(path)
        return path
