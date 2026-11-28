import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import _imports
from app.accounts.files import UserDataPaths
from app.accounts.repository import User
from app.chat.attachments import AttachmentStore, AttachmentValidationError, detect_image_info


def make_tiny_png(width: int = 4, height: int = 3) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', crc)

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    raw_rows = b''.join(b'\x00' + (b'\xff\x20\x40' * width) for _ in range(height))
    idat = zlib.compress(raw_rows)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


def make_simple_pdf(text: str = 'Hello PDF World') -> bytes:
    # Minimal valid PDF 1.4 with uncompressed BT..ET text stream
    safe = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    stream_bytes = f'BT /F1 12 Tf 72 720 Td ({safe}) Tj ET'.encode('latin-1')
    objs = [
        b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n',
        b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n',
        b'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 800] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n',
        f'4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n'.encode('ascii') + stream_bytes + b'\nendstream\nendobj\n',
        b'5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n',
    ]
    out = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode('ascii'))
    for off in offsets[1:]:
        out.extend(f'{off:010d} 00000 n \n'.encode('ascii'))
    out.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n'.encode('ascii'))
    return bytes(out)


class AttachmentStoreUnitTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.paths = UserDataPaths(Path(folder.name) / 'user_data')
        self.store = AttachmentStore(self.paths)

    def test_image_magic_bytes_and_disguised_payload_rejection(self):
        png = make_tiny_png(12, 8)
        self.assertEqual(detect_image_info(png), ('image/png', 12, 8))
        # 新账号尚未上传附件时不会提前创建 attachments 目录
        self.assertFalse(self.store.dir_for(1).exists())

        saved = self.store.save_upload(1, filename='photo.png', mime_type='image/png', data=png)
        self.assertEqual(saved.kind, 'image')
        self.assertEqual((saved.width, saved.height), (12, 8))
        self.assertTrue(self.store.image_data_url(1, saved.id).startswith('data:image/png;base64,'))
        # 跨账号读取严格隔离
        self.assertIsNone(self.store.get(2, saved.id))
        self.assertIsNone(self.store.read_bytes(2, saved.id))

        # 伪装成 .png 的 HTML / SVG 脚本必须被魔数校验拒绝
        with self.assertRaises(AttachmentValidationError):
            self.store.save_upload(1, filename='evil.png', mime_type='image/png', data=b'<svg onload=alert(1)>')

        # 伪装成 .txt 的二进制文件必须拒绝
        with self.assertRaises(AttachmentValidationError):
            self.store.save_upload(1, filename='bin.txt', mime_type='text/plain', data=b'\x89\x00\x01\x02binary')

    def test_plain_text_and_pdf_document_extraction_and_cleanup(self):
        md = self.store.save_upload(
            1, filename='notes.md', mime_type='text/markdown',
            data='# 周末采购清单\n- 咸豆浆\n- 猫粮'.encode('utf-8'),
        )
        self.assertEqual(md.kind, 'document')
        self.assertIn('咸豆浆', md.extracted_text)

        pdf = self.store.save_upload(
            1, filename='guide.pdf', mime_type='application/pdf',
            data=make_simple_pdf('Project Xiaowan Multimodal Guide'),
        )
        self.assertEqual(pdf.kind, 'document')
        self.assertIn('Xiaowan Multimodal Guide', pdf.extracted_text)

        self.store.clear_user(1)
        self.assertIsNone(self.store.get(1, md.id))
        self.assertIsNone(self.store.get(1, pdf.id))


class MultimodalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_multimodal_http_upload_vision_guard_history_compact_tag_and_quote_reattach(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.api.attachments import router as attachments_router
        from app.api.chat import router as chat_router
        from app.chat.memory import load_history
        from app.chat.scheduler import ChatSessions
        from app.llm.admin_settings import ModelSettings
        from app.llm.configuration import AdminModelRuntime
        from app.llm.personal_models import PersonalModelStore
        from app.system_config import SystemConfig
        from test_conversation_storage import SQLitePool

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        paths = UserDataPaths(Path(folder.name) / 'user_data')
        attachments = AttachmentStore(paths)
        personal_models = PersonalModelStore(paths)
        admin_runtime = AdminModelRuntime()
        pool = SQLitePool(Path(folder.name) / 'mm.sqlite')

        captured_messages = []

        class InspectingReplyAgent:
            def invoke(self, payload, config=None):
                from types import SimpleNamespace
                captured_messages.append(payload['messages'])
                return {'messages': [SimpleNamespace(content='{"reply":["看到啦，好可爱"]}', usage_metadata={'total_tokens': 12})]}

        agents = (InspectingReplyAgent(), InspectingReplyAgent())
        # 先发布支持原生视觉的 deepseek-v4-flash
        admin_runtime.publish(ModelSettings(model_merchant='deepseek', model_name='deepseek-v4-flash'), agents)

        sessions = ChatSessions(
            pool.connection,
            personal_models=personal_models,
            admin_model=admin_runtime,
            attachments=attachments,
        )
        self.addAsyncCleanup(sessions.drain)

        app = FastAPI()
        app.state.system_config = SystemConfig()
        app.state.attachments = attachments
        app.state.chat = sessions
        app.include_router(attachments_router)
        app.include_router(chat_router)

        current_user = {'user': User(1, 'Admin', 'admin')}

        @app.middleware('http')
        async def auth(request, call_next):
            request.state.user = current_user['user']
            return await call_next(request)

        png_b64 = base64.b64encode(make_tiny_png(6, 6)).decode('ascii')
        doc_b64 = base64.b64encode('会议纪要：周五下午三点评审接口'.encode('utf-8')).decode('ascii')

        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            # 1. 上传图片与轻量文档
            up_img = await client.post('/attachments', json={
                'filename': 'cat.png', 'mime_type': 'image/png', 'data_base64': png_b64,
            })
            self.assertEqual(up_img.status_code, 200)
            img_meta = up_img.json()
            self.assertEqual(img_meta['kind'], 'image')

            up_doc = await client.post('/attachments', json={
                'filename': 'memo.txt', 'mime_type': 'text/plain', 'data_base64': doc_b64,
            })
            self.assertEqual(up_doc.status_code, 200)
            doc_meta = up_doc.json()
            self.assertEqual(doc_meta['kind'], 'document')

            # 跨账号访问附件返回 404，本账号读取返回 nosniff 头
            get_ok = await client.get(img_meta['url'])
            self.assertEqual(get_ok.status_code, 200)
            self.assertEqual(get_ok.headers['x-content-type-options'], 'nosniff')
            current_user['user'] = User(2, 'Other', 'user')
            self.assertEqual((await client.get(img_meta['url'])).status_code, 404)
            current_user['user'] = User(1, 'Admin', 'admin')

            # 2. 纯图片发送（不填文字）直传 image_url 块给支持视觉的模型
            turn0 = await client.post('/chat', json={
                'user_input': '',
                'attachment_ids': [img_meta['id']],
            })
            self.assertEqual(turn0.status_code, 200)
            self.assertEqual(turn0.json()['attachments'][0]['id'], img_meta['id'])
            first_content = captured_messages[-1][0]['content']
            self.assertIsInstance(first_content, list)
            self.assertEqual(first_content[0]['type'], 'image_url')
            self.assertTrue(first_content[0]['image_url']['url'].startswith('data:image/png;base64,'))

            # 3. 下一轮普通文字对话：上一轮只以 [发送了图片：cat.png] 文本摘要进入历史，不重复塞 Base64
            turn1 = await client.post('/chat', json={'user_input': '它叫团子'})
            self.assertEqual(turn1.status_code, 200)
            second_content = captured_messages[-1][0]['content']
            self.assertIsInstance(second_content, str)
            self.assertIn('[发送了图片：cat.png]', second_content)
            self.assertNotIn('data:image/png;base64,', second_content)

            # 4. 引用上一轮图片追问（quoted_attachment_ids）：原图按需重挂给大模型，但新轮次不重复落库该附件
            turn2 = await client.post('/chat', json={
                'user_input': '【引用 你：[图片]】\n看看它的耳朵',
                'quoted_attachment_ids': [img_meta['id']],
            })
            self.assertEqual(turn2.status_code, 200)
            self.assertNotIn('attachments', turn2.json())
            third_content = captured_messages[-1][0]['content']
            self.assertIsInstance(third_content, list)
            self.assertEqual(third_content[0]['type'], 'image_url')

            # 5. 切换到不支持原生看图的通义文本模型（qwen-plus）：发图片返回友好提示 422，但发轻量文档完全可用！
            admin_runtime.publish(ModelSettings(model_merchant='tongyi', model_name='qwen-plus'), agents)
            img_rejected = await client.post('/chat', json={
                'user_input': '帮我看图',
                'attachment_ids': [img_meta['id']],
            })
            self.assertEqual(img_rejected.status_code, 422)
            self.assertIn('暂不支持图片识别', img_rejected.json()['detail']['message'])

            doc_allowed = await client.post('/chat', json={
                'user_input': '帮我看下几点开会',
                'attachment_ids': [doc_meta['id']],
            })
            self.assertEqual(doc_allowed.status_code, 200)
            fourth_content = captured_messages[-1][0]['content']
            self.assertIsInstance(fourth_content, str)
            self.assertIn('周五下午三点评审接口', fourth_content)

        history_rows = load_history(pool.connection, 1, 0)
        self.assertEqual(len(history_rows), 4)
        self.assertEqual(history_rows[0]['attachments'][0]['name'], 'cat.png')
        self.assertNotIn('attachments', history_rows[1])
        self.assertNotIn('attachments', history_rows[2])
        self.assertEqual(history_rows[3]['attachments'][0]['name'], 'memo.txt')
