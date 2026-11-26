import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _imports
from app.accounts.files import UserDataPaths
from app.accounts.portrait import (
    ManualPortrait,
    UserPortrait,
    UserPortraitStore,
    format_portrait_for_prompt,
    learn_portrait_from_turn,
)
from app.accounts.repository import User


class UserPortraitTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.paths = UserDataPaths(Path(folder.name) / 'user_data')
        self.store = UserPortraitStore(self.paths)

    def test_store_reads_empty_by_default_and_isolates_manual_and_learned_layers(self):
        empty = self.store.get(1)
        self.assertEqual(empty, UserPortrait())
        self.assertEqual(format_portrait_for_prompt(empty), '暂无')
        self.assertFalse(self.store.path_for(1).exists())

        saved = UserPortrait(
            manual=ManualPortrait(call_me='  阿明 ', city='杭州', chat_preference='吐槽时别急着给建议'),
            learned=['喜欢喝拿铁', '  喜欢喝拿铁  ', '', '养了只叫团子的橘猫'],
            auto_learn=True,
        )
        self.store.update(1, saved)
        loaded = self.store.get(1)
        self.assertEqual(loaded.manual.call_me, '阿明')
        self.assertEqual(loaded.manual.city, '杭州')
        self.assertEqual(loaded.learned, ['喜欢喝拿铁', '养了只叫团子的橘猫'])
        self.assertEqual(self.store.get(2), UserPortrait())

        # 自动记忆更新只改 learned，绝不覆盖 manual
        updated = self.store.update_learned(1, ['喜欢喝拿铁', '周末常打羽毛球'])
        self.assertEqual(updated.manual.city, '杭州')
        self.assertEqual(updated.learned, ['喜欢喝拿铁', '周末常打羽毛球'])

        prompt_text = format_portrait_for_prompt(updated)
        self.assertIn('常住城市：杭州', prompt_text)
        self.assertIn('周末常打羽毛球', prompt_text)

        # 关闭自动记录后，update_learned 不会写入
        self.store.update(1, UserPortrait(manual=updated.manual, learned=updated.learned, auto_learn=False))
        self.assertIsNone(self.store.update_learned(1, ['不应写入的印象']))
        self.assertEqual(self.store.get(1).learned, ['喜欢喝拿铁', '周末常打羽毛球'])

    def test_clear_all_learned_resets_only_learned_items_and_keeps_manual_fields(self):
        self.store.update(1, UserPortrait(
            manual=ManualPortrait(call_me='阿明', city='杭州'),
            learned=['爱喝美式', '养猫'],
            auto_learn=False,
        ))
        self.store.update(2, UserPortrait(
            manual=ManualPortrait(city='上海'),
            learned=[],
            auto_learn=True,
        ))
        self.assertEqual(self.store.clear_all_learned(), 1)
        first = self.store.get(1)
        self.assertEqual(first.manual.call_me, '阿明')
        self.assertEqual(first.manual.city, '杭州')
        self.assertEqual(first.auto_learn, False)
        self.assertEqual(first.learned, [])
        self.assertEqual(self.store.get(2).manual.city, '上海')

    def test_learn_portrait_from_turn_parses_json_and_ignores_unchanged_or_invalid(self):
        current = UserPortrait(manual=ManualPortrait(city='上海'), learned=['喜欢小猫'])
        changed_agent = SimpleNamespace(invoke=lambda _: {
            'messages': [SimpleNamespace(content='{"changed":true,"items":["喜欢小猫","爱吃咸口早饭"]}')]
        })
        self.assertEqual(
            learn_portrait_from_turn(changed_agent, current, '我早饭最爱吃咸豆浆', ['我也爱咸口']),
            ['喜欢小猫', '爱吃咸口早饭'],
        )
        unchanged_agent = SimpleNamespace(invoke=lambda _: {
            'messages': [SimpleNamespace(content='{"changed":false,"items":[]}')]
        })
        self.assertIsNone(learn_portrait_from_turn(unchanged_agent, current, '今天好冷', ['多穿点']))


class PortraitIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_portrait_http_endpoints_and_background_learning_in_chat_sessions(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.api.portrait import router as portrait_router
        from app.chat.scheduler import ChatSessions
        from test_conversation_storage import FakeAgent, SQLitePool

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        paths = UserDataPaths(Path(folder.name) / 'user_data')
        portraits = UserPortraitStore(paths)
        app = FastAPI()
        app.state.portraits = portraits
        app.include_router(portrait_router)
        user = User(7, 'Member', 'admin')

        @app.middleware('http')
        async def auth(request, call_next):
            request.state.user = user
            return await call_next(request)

        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            initial = await client.get('/portrait')
            self.assertEqual(initial.status_code, 200)
            self.assertEqual(initial.json()['auto_learn'], True)
            updated = await client.post('/portrait', json={
                'manual': {'call_me': '小陈', 'city': '苏州', 'birthday': '', 'occupation': '工程师', 'chat_preference': ''},
                'learned': ['喜欢听雨声'],
                'auto_learn': True,
            })
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()['manual']['city'], '苏州')

        pool = SQLitePool(Path(folder.name) / 'chat.sqlite')
        sessions = ChatSessions(pool.connection, portraits=portraits)
        answer_prompts = []

        def summarize_or_learn(prompt):
            if '长期印象' in prompt:
                return '{"changed":true,"items":["喜欢听雨声","养了一只叫团子的橘猫"]}'
            return 'summary'

        agents = (
            FakeAgent(lambda _: '{"intent":"分享宠物","emotion":"开心","pace":"简短","answer":"接话"}'),
            FakeAgent(summarize_or_learn),
            FakeAgent(lambda prompt: (answer_prompts.append(prompt), '{"reply":["团子这名字好可爱"]}')[1]),
        )
        with (
            patch.object(ChatSessions, '_snapshot_agents', return_value=agents),
            patch('app.chat.nodes.count_tokens', return_value=1),
        ):
            result = await sessions.send(user, '我家养了一只叫团子的橘猫')
            self.assertTrue(result['saved'])
            await sessions.drain()

        self.assertIn('常住城市：苏州', answer_prompts[0])
        self.assertIn('喜欢听雨声', answer_prompts[0])
        self.assertEqual(portraits.get(user.id).learned, ['喜欢听雨声', '养了一只叫团子的橘猫'])
        self.assertEqual(portraits.get(user.id).manual.city, '苏州')
