import base64
import binascii
from logging import WARNING
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.account_logging import log_event
from app.chat.attachments import IMAGE_MIME_TYPES, AttachmentValidationError, PublicAttachment

# Base64 编码后最大约 11.5 MB（对应 8 MB 原始 PDF 上限）
MAX_BASE64_LENGTH = 12 * 1024 * 1024


class AttachmentUploadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(default='', max_length=255)
    mime_type: str = Field(default='', max_length=120)
    data_base64: str = Field(min_length=1, max_length=MAX_BASE64_LENGTH)


router = APIRouter()


@router.post('/attachments', response_model=PublicAttachment)
def upload_attachment(req: AttachmentUploadRequest, request: Request):
    store = getattr(request.app.state, 'attachments', None)
    if store is None:
        raise HTTPException(503, '附件服务尚未启用')

    raw_b64 = req.data_base64.strip()
    if raw_b64.startswith('data:') and ',' in raw_b64:
        header, raw_b64 = raw_b64.split(',', 1)
        if not req.mime_type and ';' in header:
            req_mime = header[5:].split(';', 1)[0].strip()
        else:
            req_mime = req.mime_type
    else:
        req_mime = req.mime_type

    try:
        raw_bytes = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, '附件编码格式无效，请重新选择文件') from None

    try:
        saved = store.save_upload(
            request.state.user.id,
            filename=req.filename,
            mime_type=req_mime,
            data=raw_bytes,
        )
    except AttachmentValidationError as error:
        raise HTTPException(422, str(error)) from None
    except OSError as error:
        log_event('attachment_save_failed', level=WARNING, error=type(error).__name__)
        raise HTTPException(500, '附件保存失败，请稍后重试') from None

    return saved.to_public()


@router.get('/attachments/{attachment_id}')
def get_attachment(attachment_id: str, request: Request):
    store = getattr(request.app.state, 'attachments', None)
    if store is None:
        raise HTTPException(404, '附件不存在')

    loaded = store.read_bytes(request.state.user.id, attachment_id)
    if loaded is None:
        raise HTTPException(404, '附件不存在')

    meta, content = loaded
    encoded_name = quote(meta.name)
    if meta.kind == 'image' and meta.mime_type in IMAGE_MIME_TYPES:
        media_type = meta.mime_type
        disposition = f"inline; filename*=UTF-8''{encoded_name}"
    else:
        # 文档统一下载，避免浏览器直接解析渲染潜在的 HTML/XML 文本。
        media_type = 'application/octet-stream'
        disposition = f"attachment; filename*=UTF-8''{encoded_name}"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            'Content-Disposition': disposition,
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, max-age=86400',
        },
    )
