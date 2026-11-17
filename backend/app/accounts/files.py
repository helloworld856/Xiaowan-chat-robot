"""Paths, permissions, and atomic writes for account-owned files."""

import os
import stat
from pathlib import Path
from threading import RLock
from typing import Literal

from app.config import BACKEND_ROOT
from app.json_files import atomic_write_json


FileName = Literal['profile.json', 'model.json', 'preferences.json']


def require_directory(path: Path) -> None:
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise OSError(f'账号数据路径不是目录: {path.name}')


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


class UserDataPaths:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else BACKEND_ROOT / 'user_data'
        self._locks = tuple(RLock() for _ in range(64))

    def directory(self, user_id: int) -> Path:
        if type(user_id) is not int or user_id < 1:
            raise ValueError('账号 ID 无效')
        return self.root / str(user_id)

    def file(self, user_id: int, filename: FileName) -> Path:
        return self.directory(user_id) / filename

    def lock(self, user_id: int):
        self.directory(user_id)
        return self._locks[user_id % len(self._locks)]

    def ensure_directory(self, user_id: int) -> Path:
        directory = self.directory(user_id)
        _private_directory(self.root)
        _private_directory(directory)
        return directory

    def log_directory(self, user_id: int) -> Path:
        return self.directory(user_id) / 'logs'

    def ensure_log_directory(self, user_id: int) -> Path:
        path = self.ensure_directory(user_id) / 'logs'
        _private_directory(path)
        return path
