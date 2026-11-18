"""Atomic writes shared by file-backed stores."""

import json
import os
import re
import tempfile
from pathlib import Path


_JSON_STRING_OR_COMMENT = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\r\n]*')


def strip_json_line_comments(text: str) -> str:
    # 跳过字符串里的双斜杠，保留 URL、密码和转义字符。
    return _JSON_STRING_OR_COMMENT.sub(
        lambda match: ' ' * len(match[0]) if match[0].startswith('//') else match[0], text,
    )


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
