"""Atomic writes shared by file-backed stores."""

import json
import os
import re
import tempfile
from pathlib import Path


_JSON_STRING_OR_COMMENT = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\r\n]*')
_JSON_TOKEN = re.compile(r'\S')


def strip_json_line_comments(text: str) -> str:
    # 跳过字符串里的双斜杠，保留 URL、密码和转义字符。
    return _JSON_STRING_OR_COMMENT.sub(
        lambda match: ' ' * len(match[0]) if match[0].startswith('//') else match[0], text,
    )


def _object_fields(text: str, offset: int):
    decoder = json.JSONDecoder()
    offset = _JSON_TOKEN.search(text, offset).start() + 1
    previous = None
    while True:
        start = _JSON_TOKEN.search(text, offset).start()
        if text[start] == '}':
            return
        key, after_key = decoder.raw_decode(text, start)
        colon = _JSON_TOKEN.search(text, after_key).start()
        value_start = _JSON_TOKEN.search(text, colon + 1).start()
        _, end = decoder.raw_decode(text, value_start)
        separator = _JSON_TOKEN.search(text, end).start()
        following = separator if text[separator] == ',' else None
        yield key, start, value_start, end, previous, following
        if following is None:
            return
        previous = following
        offset = following + 1


def remove_json_section_field(text: str, section: str, field: str) -> str:
    """Remove a member from already validated JSONC while preserving surrounding comments."""
    while True:
        parsed = strip_json_line_comments(text)
        fields = (entry for key, _, value_start, _, _, _ in _object_fields(parsed, 0)
                  if key == section and parsed[value_start] == '{'
                  for entry in _object_fields(parsed, value_start) if entry[0] == field)
        found = next(fields, None)
        if found is None:
            return text
        _, start, _, end, previous, following = found
        # 有后续成员时去掉后侧逗号，末尾成员则去掉前侧逗号。
        if following is not None:
            text = text[:start] + text[end:following] + text[following + 1:]
        elif previous is not None:
            text = text[:previous] + text[previous + 1:start] + text[end:]
        else:
            text = text[:start] + text[end:]


def dotenv_assignment(name: str, value: str) -> str:
    quoted = value.replace('\\', '\\\\').replace("'", "\\'")
    return f"{name}='{quoted}'\n"


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
