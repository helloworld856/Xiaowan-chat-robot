"""Account-isolated multimodal attachments: safe image verification, lightweight document text extraction, and storage."""

import base64
import os
import re
import struct
import zlib
from pathlib import Path
from secrets import token_hex
from shutil import rmtree
from time import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.account_logging import logger
from app.accounts.files import UserDataPaths, _private_directory, read_text, replace_json, require_directory, require_file

AttachmentKind = Literal['image', 'document']

ATTACHMENT_ID_PATTERN = r'^att_[0-9a-f]{16}$'
ATTACHMENT_ID_RE = re.compile(ATTACHMENT_ID_PATTERN)

MAX_ATTACHMENTS_PER_TURN = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 25
MAX_DOCUMENT_TEXT_CHARS = 6000
MAX_IMAGE_DIMENSION = 16384

IMAGE_MIME_TYPES = frozenset(('image/png', 'image/jpeg', 'image/webp', 'image/gif'))
TEXT_EXTENSIONS = frozenset((
    '.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.yaml', '.yml', '.log',
    '.py', '.js', '.ts', '.tsx', '.jsx', '.vue', '.html', '.htm', '.css', '.sql', '.xml', '.ini', '.toml',
))


class AttachmentValidationError(ValueError):
    pass


class PublicAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=ATTACHMENT_ID_PATTERN)
    kind: AttachmentKind
    name: str = Field(min_length=1, max_length=120)
    mime_type: str = Field(min_length=1, max_length=80)
    size: int = Field(ge=1)
    width: int | None = None
    height: int | None = None
    url: str = Field(min_length=1, max_length=160)


class StoredAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=ATTACHMENT_ID_PATTERN)
    kind: AttachmentKind
    name: str = Field(min_length=1, max_length=120)
    mime_type: str = Field(min_length=1, max_length=80)
    size: int = Field(ge=1)
    width: int | None = None
    height: int | None = None
    extracted_text: str = ''
    truncated: bool = False
    created_at: int

    def to_public(self) -> PublicAttachment:
        return PublicAttachment(
            id=self.id,
            kind=self.kind,
            name=self.name,
            mime_type=self.mime_type,
            size=self.size,
            width=self.width,
            height=self.height,
            url=f'/attachments/{self.id}',
        )


def _sanitize_filename(raw: str, fallback: str) -> str:
    cleaned = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', '_', (raw or '').strip()).strip('. ')
    if not cleaned:
        return fallback
    if len(cleaned) > 120:
        stem, dot, ext = cleaned.rpartition('.')
        if dot and len(ext) <= 12:
            cleaned = stem[:120 - len(ext) - 1] + '.' + ext
        else:
            cleaned = cleaned[:120]
    return cleaned


def detect_image_info(data: bytes) -> tuple[str, int, int] | None:
    """用文件头魔数精确识别图片格式与宽高，拒绝伪装成图片的 HTML/SVG/脚本。"""
    n = len(data)
    if n >= 24 and data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
        return 'image/png', width, height

    if n >= 10 and data[:6] in (b'GIF87a', b'GIF89a'):
        width, height = struct.unpack('<HH', data[6:10])
        return 'image/gif', width, height

    if n >= 30 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        chunk = data[12:16]
        if chunk == b'VP8 ' and n >= 30:
            raw_w, raw_h = struct.unpack('<HH', data[26:30])
            return 'image/webp', raw_w & 0x3FFF, raw_h & 0x3FFF
        if chunk == b'VP8L' and n >= 25:
            bits = struct.unpack('<I', data[21:25])[0]
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return 'image/webp', width, height
        if chunk == b'VP8X' and n >= 30:
            width = int.from_bytes(data[24:27], 'little') + 1
            height = int.from_bytes(data[27:30], 'little') + 1
            return 'image/webp', width, height

    if n >= 4 and data[:2] == b'\xff\xd8':
        idx = 2
        while idx + 4 <= n:
            if data[idx] != 0xFF:
                idx += 1
                continue
            while idx < n and data[idx] == 0xFF:
                idx += 1
            if idx >= n:
                break
            marker = data[idx]
            idx += 1
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if idx + 2 > n:
                break
            seg_len = struct.unpack('>H', data[idx:idx + 2])[0]
            if seg_len < 2 or idx + seg_len > n:
                break
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                if seg_len >= 7:
                    height, width = struct.unpack('>HH', data[idx + 3:idx + 7])
                    return 'image/jpeg', width, height
                break
            idx += seg_len

    return None


def _clean_document_text(text: str) -> tuple[str, bool]:
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = [re.sub(r'[ \t\f\v]+', ' ', line).rstrip() for line in normalized.split('\n')]
    # 合并连续超过 2 行的空行，保持段落可读。
    compact_lines: list[str] = []
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                compact_lines.append('')
        else:
            blank_run = 0
            compact_lines.append(line)
    joined = '\n'.join(compact_lines).strip()
    if len(joined) > MAX_DOCUMENT_TEXT_CHARS:
        return joined[:MAX_DOCUMENT_TEXT_CHARS].rstrip(), True
    return joined, False


def _extract_pdf_fallback(data: bytes) -> str:
    """无第三方 PDF 库时的轻量流解压文本兜底，解析 Flate 流与明文括号字符串。"""
    streams: list[bytes] = [data]
    for match in re.finditer(rb'stream\r?\n([\s\S]*?)\r?\nendstream', data):
        raw_stream = match.group(1)
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                streams.append(zlib.decompress(raw_stream, wbits))
                break
            except zlib.error:
                continue

    pieces: list[str] = []
    for buf in streams:
        for block in re.findall(rb'BT[\s\S]*?ET', buf):
            for lit in re.findall(rb'\((?:\\.|[^\\()])*\)', block):
                inner = lit[1:-1]
                inner = (
                    inner.replace(rb'\n', b'\n')
                    .replace(rb'\r', b'\r')
                    .replace(rb'\t', b'\t')
                    .replace(rb'\(', b'(')
                    .replace(rb'\)', b')')
                    .replace(rb'\\', b'\\')
                )
                if inner.startswith(b'\xfe\xff'):
                    decoded = inner[2:].decode('utf-16-be', errors='ignore')
                else:
                    decoded = inner.decode('utf-8', errors='ignore')
                if decoded.strip():
                    pieces.append(decoded)
            for hex_tok in re.findall(rb'<([0-9A-Fa-f]{4,})>', block):
                if len(hex_tok) % 2 == 0:
                    raw_hex = bytes.fromhex(hex_tok.decode('ascii'))
                    if raw_hex.startswith(b'\xfe\xff') or len(raw_hex) % 2 == 0:
                        decoded = raw_hex.decode('utf-16-be', errors='ignore')
                        if decoded.strip() and any(ch.isprintable() for ch in decoded):
                            pieces.append(decoded)
    return '\n'.join(pieces)


def extract_pdf_text(data: bytes) -> tuple[str, bool]:
    if len(data) > MAX_PDF_BYTES:
        raise AttachmentValidationError('PDF 文件最大支持 8 MB')
    if not data.startswith(b'%PDF-'):
        raise AttachmentValidationError('PDF 文件格式无效')

    extracted_pages: list[str] = []
    used_engine = False

    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
    except ImportError:
        pdfium = None

    if pdfium is not None:
        used_engine = True
        doc = None
        try:
            doc = pdfium.PdfDocument(data)
            page_count = min(len(doc), MAX_PDF_PAGES)
            total_chars = 0
            for idx in range(page_count):
                page = doc[idx]
                textpage = page.get_textpage()
                try:
                    page_text = textpage.get_text_range() or ''
                finally:
                    textpage.close()
                    page.close()
                if page_text.strip():
                    extracted_pages.append(page_text)
                    total_chars += len(page_text)
                    if total_chars >= MAX_DOCUMENT_TEXT_CHARS + 500:
                        break
        except Exception as error:
            logger.warning('pypdfium2 PDF 解析失败（%s）', type(error).__name__)
            raise AttachmentValidationError('无法读取该 PDF 文件（文件可能已加密或损坏）') from None
        finally:
            if doc is not None:
                doc.close()

    if not used_engine:
        try:
            import pypdf  # type: ignore[import-not-found]
        except ImportError:
            pypdf = None
        if pypdf is not None:
            import io
            used_engine = True
            try:
                reader = pypdf.PdfReader(io.BytesIO(data))
                for page in reader.pages[:MAX_PDF_PAGES]:
                    page_text = page.extract_text() or ''
                    if page_text.strip():
                        extracted_pages.append(page_text)
            except Exception:
                raise AttachmentValidationError('无法读取该 PDF 文件（文件可能已加密或损坏）') from None

    raw_text = '\n\n'.join(extracted_pages) if used_engine else _extract_pdf_fallback(data)
    cleaned, truncated = _clean_document_text(raw_text)
    if not cleaned:
        raise AttachmentValidationError('该 PDF 未提取到文字内容（可能是扫描件），请截图后作为图片发送')
    return cleaned, truncated


def extract_plain_document_text(data: bytes) -> tuple[str, bool]:
    if len(data) > MAX_TEXT_FILE_BYTES:
        raise AttachmentValidationError('文本类文档最大支持 2 MB')
    if not data:
        raise AttachmentValidationError('文件内容为空')

    decoded: str | None = None
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        try:
            decoded = data.decode('utf-16')
        except UnicodeDecodeError:
            decoded = None
    else:
        if b'\x00' in data[:8192]:
            raise AttachmentValidationError('不支持二进制文件，请上传图片、PDF 或常见文本文件')
        for enc in ('utf-8-sig', 'gb18030'):
            try:
                decoded = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue

    if decoded is None:
        raise AttachmentValidationError('无法识别该文档的字符编码，请保存为 UTF-8 后重试')

    cleaned, truncated = _clean_document_text(decoded)
    if not cleaned:
        raise AttachmentValidationError('文档中没有可读取的文字内容')
    return cleaned, truncated


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    require_directory(path.parent)
    tmp = path.parent / f'.{path.name}.{token_hex(4)}.tmp'
    try:
        tmp.write_bytes(data)
        if os.name == 'posix':
            tmp.chmod(0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class AttachmentStore:
    """按账号隔离读写聊天图片与文档附件，首次上传时才创建 attachments 子目录。"""

    def __init__(self, paths: UserDataPaths):
        self.paths = paths

    def dir_for(self, user_id: int) -> Path:
        return self.paths.directory(user_id) / 'attachments'

    def _ensure_dir(self, user_id: int) -> Path:
        account_dir = self.paths.ensure_directory(user_id)
        att_dir = account_dir / 'attachments'
        _private_directory(att_dir)
        return att_dir

    @staticmethod
    def _validate_id(attachment_id: str) -> str:
        if not isinstance(attachment_id, str) or not ATTACHMENT_ID_RE.fullmatch(attachment_id):
            raise AttachmentValidationError('附件标识无效')
        return attachment_id

    def save_upload(self, user_id: int, *, filename: str, mime_type: str = '', data: bytes) -> StoredAttachment:
        if not data:
            raise AttachmentValidationError('上传的文件为空')

        ext = Path(filename or '').suffix.lower()
        normalized_mime = (mime_type or '').split(';', 1)[0].strip().lower()

        img_info = detect_image_info(data)
        if img_info is not None:
            detected_mime, width, height = img_info
            if len(data) > MAX_IMAGE_BYTES:
                raise AttachmentValidationError('单张图片最大支持 5 MB')
            if width <= 0 or height <= 0 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                raise AttachmentValidationError('图片尺寸无效或超出限制')
            default_ext = '.' + detected_mime.split('/', 1)[1].replace('jpeg', 'jpg')
            safe_name = _sanitize_filename(filename, f'image{default_ext}')
            kind: AttachmentKind = 'image'
            effective_mime = detected_mime
            extracted_text = ''
            truncated = False
        elif normalized_mime in IMAGE_MIME_TYPES or ext in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.svg'):
            raise AttachmentValidationError('仅支持 PNG、JPG、WebP 或 GIF 格式的有效图片')
        elif ext == '.pdf' or normalized_mime == 'application/pdf' or data.startswith(b'%PDF-'):
            safe_name = _sanitize_filename(filename, 'document.pdf')
            kind = 'document'
            effective_mime = 'application/pdf'
            width = height = None
            extracted_text, truncated = extract_pdf_text(data)
        elif ext in TEXT_EXTENSIONS or normalized_mime.startswith('text/'):
            safe_name = _sanitize_filename(filename, 'document.txt')
            kind = 'document'
            effective_mime = 'text/plain'
            width = height = None
            extracted_text, truncated = extract_plain_document_text(data)
        else:
            raise AttachmentValidationError('暂不支持该文件格式，请上传图片（PNG/JPG/WebP/GIF）、PDF 或常见文本文件')

        att_id = f'att_{token_hex(8)}'
        record = StoredAttachment(
            id=att_id,
            kind=kind,
            name=safe_name,
            mime_type=effective_mime,
            size=len(data),
            width=width,
            height=height,
            extracted_text=extracted_text,
            truncated=truncated,
            created_at=int(time() * 1000),
        )
        with self.paths.lock(user_id):
            folder = self._ensure_dir(user_id)
            _atomic_write_bytes(folder / f'{att_id}.bin', data)
            replace_json(folder / f'{att_id}.meta.json', record.model_dump())
        return record

    def get(self, user_id: int, attachment_id: str) -> StoredAttachment | None:
        try:
            att_id = self._validate_id(attachment_id)
        except AttachmentValidationError:
            return None
        folder = self.dir_for(user_id)
        meta_path = folder / f'{att_id}.meta.json'
        bin_path = folder / f'{att_id}.bin'
        with self.paths.lock(user_id):
            try:
                require_file(meta_path)
                require_file(bin_path)
                return StoredAttachment.model_validate_json(read_text(meta_path))
            except (FileNotFoundError, OSError, ValueError):
                return None

    def read_bytes(self, user_id: int, attachment_id: str) -> tuple[StoredAttachment, bytes] | None:
        try:
            att_id = self._validate_id(attachment_id)
        except AttachmentValidationError:
            return None
        folder = self.dir_for(user_id)
        meta_path = folder / f'{att_id}.meta.json'
        bin_path = folder / f'{att_id}.bin'
        with self.paths.lock(user_id):
            try:
                require_file(meta_path)
                require_file(bin_path)
                meta = StoredAttachment.model_validate_json(read_text(meta_path))
                return meta, bin_path.read_bytes()
            except (FileNotFoundError, OSError, ValueError):
                return None

    def image_data_url(self, user_id: int, attachment_id: str) -> str | None:
        loaded = self.read_bytes(user_id, attachment_id)
        if loaded is None:
            return None
        meta, raw = loaded
        if meta.kind != 'image' or meta.mime_type not in IMAGE_MIME_TYPES:
            return None
        encoded = base64.b64encode(raw).decode('ascii')
        return f'data:{meta.mime_type};base64,{encoded}'

    def clear_user(self, user_id: int) -> None:
        folder = self.dir_for(user_id)
        with self.paths.lock(user_id):
            try:
                require_directory(folder)
                rmtree(folder)
            except FileNotFoundError:
                return
            except OSError as error:
                logger.warning('暂时无法清理账号 %d 的附件目录（%s）', user_id, type(error).__name__)

    def clear_all(self) -> int:
        try:
            require_directory(self.paths.root)
            entries = list(self.paths.root.iterdir())
        except FileNotFoundError:
            return 0
        cleared = 0
        for entry in entries:
            if entry.name.startswith('.'):
                continue
            att_dir = entry / 'attachments'
            try:
                require_directory(entry)
                require_directory(att_dir)
                rmtree(att_dir)
                cleared += 1
            except FileNotFoundError:
                continue
            except OSError as error:
                logger.warning('无法清理账号目录 %s 的附件（%s）', entry.name, type(error).__name__)
        return cleared
