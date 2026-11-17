"""Atomic writes shared by JSON-backed stores."""

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path: Path, value: object) -> None:
    temporary = None
    try:
        # 临时文件与目标同目录，写完并同步后再原子替换。
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.stem}-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
