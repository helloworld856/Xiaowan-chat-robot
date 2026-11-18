"""Atomic writes shared by file-backed stores."""

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path: Path, value: object, *, replace: bool = True) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n', replace=replace)


def backup_provider_migration(path: Path, text: str) -> None:
    try:
        atomic_write_text(path.with_name(path.name + '.before-provider.bak'), text, replace=False)
    except FileExistsError:
        pass  # 重试时保留首次迁移前的版本。


def atomic_write_text(path: Path, text: str, *, replace: bool = True) -> None:
    temporary = None
    try:
        # 临时文件与目标同目录，写完并同步后再原子替换。
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.stem.lstrip(".")}-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        if replace:
            os.replace(temporary, path)
        elif os.name == 'nt':
            os.rename(temporary, path)
        else:
            # 首次迁移时只创建目标，避免覆盖同时写入的配置。
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
