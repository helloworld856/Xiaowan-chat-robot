import importlib.util
import json
import tempfile
import unittest
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from _imports import BACKEND_ROOT as ROOT
from app.chat.episodes import EpisodeMemoryStore
from app.chat.memory import MemoryManager, load_history
from app.database.mysql import reset_chat_history, reset_episode_memories
from app.llm.embeddings import LocalEmbeddingModel, cosine_similarity, pack_vector, unpack_vector
from app.system_config import EmbeddingMemoryConfig, MemoryConfig
from test_conversation_storage import FakeAgent, SQLitePool, message


class DeterministicEmbedder:
    """Maps bilingual topic keywords onto orthogonal 6D unit axes for deterministic hybrid-recall tests."""

    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled

    def _vectorize(self, text: str) -> np.ndarray | None:
        if not self.enabled:
            return None
        lowered = text.lower()
        vec = np.zeros(6, dtype=np.float32)
        if any(tok in lowered for tok in ('面试', '秋招', '字节', 'offer', 'interview', 'bytedance', 'react', 'job')):
            vec[0] += 1.0
        if any(tok in lowered for tok in ('雅思', '口语', '考试', 'ielts', 'speaking', 'exam')):
            vec[1] += 1.0
        if any(tok in lowered for tok in ('火锅', '面', '早饭', '汤', 'hotpot', 'noodle')):
            vec[2] += 1.0
        if any(tok in lowered for tok in ('绿萝', '植物', '花盆', 'plant')):
            vec[3] += 1.0
        if any(tok in lowered for tok in ('旧计划', 'legacy')):
            vec[4] += 1.0
        if float(np.linalg.norm(vec)) == 0.0:
            vec[5] = 1.0
        return (vec / float(np.linalg.norm(vec))).astype('<f4')

    def encode_query(self, text: str) -> np.ndarray | None:
        return self._vectorize(text)

    def encode_passages(self, texts: list[str]) -> list[np.ndarray] | None:
        if not self.enabled:
            return None
        return [self._vectorize(item) for item in texts]


class EmbeddingEngineTests(unittest.TestCase):
    def test_pack_unpack_and_cosine_similarity_guard_invalid_inputs(self):
        original = np.array([0.6, 0.8, 0.0], dtype=np.float32)
        blob, dim = pack_vector(original)
        self.assertEqual(dim, 3)
        restored = unpack_vector(blob, dim)
        self.assertIsNotNone(restored)
        np.testing.assert_allclose(restored, original, atol=1e-6)
        self.assertAlmostEqual(cosine_similarity(original, restored), 1.0, places=6)

        self.assertIsNone(unpack_vector(None, 3))
        self.assertIsNone(unpack_vector(blob, 2))
        self.assertIsNone(unpack_vector(blob, 0))
        nan_blob, _ = pack_vector(np.array([1.0, np.nan], dtype=np.float32))
        self.assertIsNone(unpack_vector(nan_blob, 2))
        self.assertEqual(cosine_similarity(np.array([1.0, 0.0]), np.array([-1.0, 0.0])), 0.0)
        self.assertEqual(cosine_similarity(np.array([1.0, 0.0]), np.array([1.0, 0.0, 0.0])), 0.0)

    def test_missing_or_disabled_model_falls_back_cleanly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            disabled = LocalEmbeddingModel(EmbeddingMemoryConfig(enabled=False), root=root)
            self.assertFalse(disabled.available)
            self.assertIsNone(disabled.encode_query('hello'))
            self.assertIsNone(disabled.encode_passages(['hello']))

            missing = LocalEmbeddingModel(EmbeddingMemoryConfig(enabled=True, model_dir='models/missing'), root=root)
            self.assertFalse(missing.available)
            self.assertIsNone(missing.encode_query('今天天气怎么样'))
            self.assertEqual(missing.encode_passages([]), [])
            self.assertIsNone(missing.encode_passages(['测试文本']))

    def test_onnx_inference_applies_prefixes_attention_mask_pooling_and_l2_norm(self):
        with tempfile.TemporaryDirectory() as folder:
            model_dir = Path(folder) / 'models' / 'multilingual-e5-small'
            model_dir.mkdir(parents=True)
            (model_dir / 'model.onnx').write_bytes(b'placeholder')
            (model_dir / 'tokenizer.json').write_text('{}', encoding='utf-8')

            observed_texts: list[list[str]] = []
            observed_feeds: list[dict] = []

            class FakeTokenizer:
                def enable_truncation(self, max_length):
                    self.max_length = max_length

                def enable_padding(self, pad_id, pad_token):
                    self.pad_id = pad_id

                def encode_batch(self, texts):
                    observed_texts.append(list(texts))
                    # 第二条样本包含末尾 padding，验证掩码均值池化会忽略 pad 位置。
                    return [
                        SimpleNamespace(ids=[10, 20, 0], attention_mask=[1, 1, 0]),
                    ] * len(texts)

            class FakeSession:
                def get_inputs(self):
                    return [
                        SimpleNamespace(name='input_ids'),
                        SimpleNamespace(name='attention_mask'),
                        SimpleNamespace(name='token_type_ids'),
                    ]

                def run(self, _output_names, feeds):
                    observed_feeds.append(feeds)
                    batch = feeds['input_ids'].shape[0]
                    hidden = np.tile(
                        np.array([[[3.0, 0.0], [3.0, 8.0], [999.0, 999.0]]], dtype=np.float32),
                        (batch, 1, 1),
                    )
                    return [hidden]

            fake_ort = SimpleNamespace(
                SessionOptions=lambda: SimpleNamespace(intra_op_num_threads=1, inter_op_num_threads=1),
                InferenceSession=lambda *args, **kwargs: FakeSession(),
            )
            fake_tok_mod = SimpleNamespace(Tokenizer=SimpleNamespace(from_file=lambda _p: FakeTokenizer()))

            engine = LocalEmbeddingModel(EmbeddingMemoryConfig(), root=Path(folder))
            with patch.dict('sys.modules', {'onnxruntime': fake_ort, 'tokenizers': fake_tok_mod}):
                q_vec = engine.encode_query('React 面试准备')
                p_vecs = engine.encode_passages(['下周去面试'])

            self.assertEqual(observed_texts[0], ['query: React 面试准备'])
            self.assertEqual(observed_texts[1], ['passage: 下周去面试'])
            self.assertIn('token_type_ids', observed_feeds[0])
            # 有效 token 为 [3, 0] 与 [3, 8]，均值为 [3, 4]，L2 归一化后应为 [0.6, 0.8]，999 padding 被完全屏蔽。
            np.testing.assert_allclose(q_vec, np.array([0.6, 0.8], dtype=np.float32), atol=1e-6)
            self.assertEqual(len(p_vecs), 1)
            np.testing.assert_allclose(p_vecs[0], np.array([0.6, 0.8], dtype=np.float32), atol=1e-6)

    def test_download_helper_falls_back_to_mirror_and_startup_auto_downloads_when_missing(self):
        from app.llm.embeddings import DEFAULT_MIRROR_ENDPOINT, download_embedding_model_files

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src_dir = root / 'hf_cache'
            src_dir.mkdir()
            tok_src = src_dir / 'tok.json'
            onnx_src = src_dir / 'quant.onnx'
            tok_src.write_text('{"version":"1.0"}', encoding='utf-8')
            onnx_src.write_bytes(b'fake-onnx-binary')

            calls: list[tuple[str, str | None]] = []

            def fake_hf_download(*, repo_id, filename, endpoint=None, etag_timeout=8):
                calls.append((filename, endpoint))
                if endpoint is None:
                    raise ConnectionError('official huggingface unreachable')
                if filename == 'tokenizer.json':
                    return str(tok_src)
                if filename == 'onnx/model_quantized.onnx':
                    return str(onnx_src)
                raise FileNotFoundError(filename)

            fake_hf = SimpleNamespace(hf_hub_download=fake_hf_download)
            target_dir = root / 'models' / 'multilingual-e5-small'
            with (
                patch.dict('sys.modules', {'huggingface_hub': fake_hf}),
                patch.dict('os.environ', {}, clear=False),
            ):
                os_env_backup = __import__('os').environ.pop('HF_ENDPOINT', None)
                try:
                    onnx_dest, tok_dest = download_embedding_model_files('Xenova/multilingual-e5-small', target_dir)
                finally:
                    if os_env_backup is not None:
                        __import__('os').environ['HF_ENDPOINT'] = os_env_backup

            self.assertTrue(onnx_dest.is_file())
            self.assertTrue(tok_dest.is_file())
            # 先尝试默认源失败，自动回退到镜像源成功
            self.assertEqual(calls[0], ('tokenizer.json', None))
            self.assertIn(('tokenizer.json', DEFAULT_MIRROR_ENDPOINT), calls)
            self.assertIn(('onnx/model_quantized.onnx', DEFAULT_MIRROR_ENDPOINT), calls)

            # 模型已存在时，prepare_on_startup 不再重复触发下载
            existing_engine = LocalEmbeddingModel(EmbeddingMemoryConfig(), root=root)
            with (
                patch('app.llm.embeddings.download_embedding_model_files') as dl_mock,
                patch.object(LocalEmbeddingModel, '_ensure_loaded', return_value=True) as load_mock,
            ):
                existing_engine.prepare_on_startup(blocking=True)
                dl_mock.assert_not_called()
                load_mock.assert_called_once()

            # 模型不存在时，prepare_on_startup 自动下载并在完成后加载模型
            empty_root = root / 'fresh_env'
            missing_engine = LocalEmbeddingModel(EmbeddingMemoryConfig(), root=empty_root)
            self.assertFalse(missing_engine.has_model_files())

            def materialize_files(repo_id, out_dir, **kwargs):
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / 'tokenizer.json').write_text('{}', encoding='utf-8')
                (out_dir / 'model.onnx').write_bytes(b'onnx')
                return out_dir / 'model.onnx', out_dir / 'tokenizer.json'

            with (
                patch('app.llm.embeddings.download_embedding_model_files', side_effect=materialize_files) as dl_mock,
                patch.object(LocalEmbeddingModel, '_ensure_loaded', return_value=True) as load_mock,
            ):
                missing_engine.prepare_on_startup(blocking=True)
                dl_mock.assert_called_once()
                load_mock.assert_called_once()
                self.assertTrue(missing_engine.has_model_files())


class EpisodeMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pool = SQLitePool(Path(folder.name) / 'episodes.sqlite')

    def test_extract_batch_preserves_history_updates_ongoing_advances_watermark_and_prunes(self):
        config = MemoryConfig(
            recent_window_rounds=2,
            extract_batch_rounds=2,
            max_episodes_per_account=10,
            embedding=EmbeddingMemoryConfig(query_prefix='', passage_prefix='', model_dir='custom'),
        )
        store = EpisodeMemoryStore(self.pool.connection, config=config, embedder=DeterministicEmbedder())
        account_a = MemoryManager(1, self.pool.connection)
        account_b = MemoryManager(2, self.pool.connection)
        for r in range(6):
            self.assertTrue(account_a.record_message({**message(r, f'A-turn-{r}'), 'created_at': 1764150000000 + r * 1000}))
        for r in range(3):
            self.assertTrue(account_b.record_message({**message(r, f'B-turn-{r}'), 'created_at': 1764150000000}))

        # 预先写入 8 条普通事件（importance=3），便于验证总上限 10 条时的优先级淘汰
        with self.pool.connection() as db, db.cursor() as cursor:
            for idx in range(8):
                cursor.execute(
                    'INSERT INTO memory_episodes (user_id, happened_at, summary, keywords, status, importance, '
                    'source_round_start, source_round_end, embedding, embedding_dim, created_at, updated_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (1, '2026-11-01', f'常规历史事项-{idx}', '["常规"]', 'fact', 3, None, None, None, None, 1000 + idx, 1000 + idx),
                )
            db.commit()

        # 第一批（轮次 0..1）：新增 1 条 ongoing 与 1 条低重要度 fact（此时共 10 条）
        first_agent = FakeAgent(lambda prompt: (
            self.assertIn('A-turn-0', prompt),
            self.assertIn('A-turn-1', prompt),
            self.assertNotIn('A-turn-2', prompt),
            self.assertNotIn('B-turn-0', prompt),
            json.dumps({
                'episodes': [
                    {
                        'happened_at': '2026-11-20',
                        'summary': '正在准备下周的 ByteDance 前端 React 面试',
                        'keywords': ['面试', '字节跳动', 'React', 'interview', 'ByteDance'],
                        'status': 'ongoing',
                        'importance': 5,
                    },
                    {
                        'happened_at': '2026-11-20',
                        'summary': '中午点了一份番茄鸡蛋面',
                        'keywords': ['午饭', '面条', 'noodle'],
                        'status': 'fact',
                        'importance': 1,
                    },
                ],
                'updates': [],
            }, ensure_ascii=False),
        )[-1])
        changed_first = store.extract_and_save_batch(1, first_agent, max_eligible_round=3)
        self.assertEqual(changed_first, 2)
        self.assertEqual(store.get_watermark(1), 1)
        # 历史原文不因归档而被删除
        self.assertEqual(len(load_history(self.pool.connection, 1, 0)), 6)

        episodes_after_first = store.list_episodes(1)
        ongoing_ep = next(ep for ep in episodes_after_first if ep.status == 'ongoing')

        # 第二批（轮次 2..3）：将之前的 ongoing 事件更新为 resolved，并新增 2 条事件触发容量淘汰（上限 10 条）
        second_agent = FakeAgent(lambda prompt: (
            self.assertIn(f'id={ongoing_ep.id}', prompt),
            json.dumps({
                'episodes': [
                    {
                        'happened_at': '2026-11-22',
                        'summary': '报名了下个月的 IELTS 雅思口语考试',
                        'keywords': ['雅思', '口语', 'IELTS', 'exam'],
                        'status': 'ongoing',
                        'importance': 4,
                    },
                    {
                        'happened_at': '2026-11-22',
                        'summary': '阳台新买了一盆绿萝',
                        'keywords': ['绿萝', 'plant'],
                        'status': 'fact',
                        'importance': 4,
                    },
                ],
                'updates': [
                    {
                        'id': ongoing_ep.id,
                        'summary': 'ByteDance 前端 React 面试通过并拿到了 offer',
                        'status': 'resolved',
                    },
                ],
            }, ensure_ascii=False),
        )[-1])
        changed_second = store.extract_and_save_batch(1, second_agent, max_eligible_round=3)
        self.assertEqual(changed_second, 3)
        self.assertEqual(store.get_watermark(1), 3)
        # 没有更多可提炼批次
        self.assertEqual(store.unextracted_batch(1, max_eligible_round=3), [])
        self.assertEqual(len(load_history(self.pool.connection, 1, 0)), 6)

        final_episodes = store.list_episodes(1)
        self.assertEqual(len(final_episodes), 10)
        summaries = [ep.summary for ep in final_episodes]
        # 低重要度（importance=1）的「番茄鸡蛋面」和最早的常规事项-0 被优先淘汰，重要事件和 ongoing 事件均被保留
        self.assertNotIn('中午点了一份番茄鸡蛋面', summaries)
        self.assertNotIn('常规历史事项-0', summaries)
        updated_interview = next(ep for ep in final_episodes if ep.id == ongoing_ep.id)
        self.assertEqual(updated_interview.status, 'resolved')
        self.assertIn('拿到了 offer', updated_interview.summary)
        self.assertEqual(store.list_episodes(2), [])

    def test_two_way_hybrid_recall_supports_cross_lingual_hits_window_exclusion_and_keyword_fallback(self):
        now_ms = 1_764_200_000_000
        day_ms = 86_400_000
        config = MemoryConfig(
            ongoing_recall_limit=2,
            ongoing_window_days=14,
            relevant_recall_limit=2,
            min_relevance_score=0.35,
            embedding=EmbeddingMemoryConfig(query_prefix='', passage_prefix='', model_dir='custom'),
        )
        embedder = DeterministicEmbedder(enabled=True)
        store = EpisodeMemoryStore(self.pool.connection, config=config, embedder=embedder)

        def insert_ep(user_id, happened_at, summary, keywords, status, importance, r_end, age_days):
            vec = embedder.encode_passages([f"{summary} {' '.join(keywords)}"])[0]
            blob, dim = pack_vector(vec)
            ts = now_ms - age_days * day_ms
            with self.pool.connection() as db, db.cursor() as cursor:
                cursor.execute(
                    'INSERT INTO memory_episodes (user_id, happened_at, summary, keywords, status, importance, '
                    'source_round_start, source_round_end, embedding, embedding_dim, created_at, updated_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (user_id, happened_at, summary, json.dumps(keywords, ensure_ascii=False), status, importance,
                     0, r_end, blob, dim, ts, ts),
                )
                db.commit()

        insert_ep(1, '2026-11-24', '正在准备下周的 IELTS 雅思口语考试', ['雅思', 'IELTS', 'exam'], 'ongoing', 4, 4, 2)
        insert_ep(1, '2026-10-01', '很久以前未完结的旧计划', ['旧计划'], 'ongoing', 5, 1, 45)
        insert_ep(1, '2026-11-10', '秋招通过了 ByteDance 前端面试拿到 offer',
                  ['秋招', '字节跳动', 'ByteDance', 'interview', 'offer'], 'resolved', 5, 6, 10)
        insert_ep(1, '2026-11-25', '刚在窗口内聊过的火锅聚餐', ['火锅', 'hotpot'], 'fact', 4, 15, 1)
        insert_ep(1, '2026-11-12', '给阳台的绿萝换了花盆', ['绿萝', 'plant'], 'fact', 3, 5, 8)
        insert_ep(2, '2026-11-25', '另一个账号的私密面试记录', ['面试', 'interview'], 'ongoing', 5, 2, 1)

        # 1. 日常无关提问：只带出近 14 天内的 ongoing 事件，不混入超期 ongoing 或无关历史事件
        unrelated = store.recall(1, '今天下午有点犯困', exclude_from_round=10, now_ms=now_ms)
        self.assertIn('IELTS 雅思口语考试', unrelated)
        self.assertIn('· 进行中', unrelated)
        self.assertNotIn('旧计划', unrelated)
        self.assertNotIn('绿萝', unrelated)
        self.assertNotIn('ByteDance', unrelated)

        # 2. 纯英文跨语言提问：命中中文记录的 ByteDance 面试事件，同时不重复召回仍在窗口内（r_end=15 >= 10）的记录
        cross_lingual = store.recall(
            1, 'How did my ByteDance job interview go?', exclude_from_round=10, now_ms=now_ms,
        )
        self.assertIn('IELTS 雅思口语考试', cross_lingual)
        self.assertIn('ByteDance 前端面试拿到 offer', cross_lingual)
        self.assertNotIn('另一个账号', cross_lingual)

        # 3. 仍在短期窗口内的火锅事件（source_round_end=15 >= exclude_from_round=10）被排除，避免与短期对话重复
        in_window = store.recall(1, '我想吃火锅 hotpot', exclude_from_round=10, now_ms=now_ms)
        self.assertNotIn('刚在窗口内聊过的火锅聚餐', in_window)

        # 4. 账号隔离：账号 2 绝不会召回账号 1 的事件
        user2_recalled = store.recall(2, 'ByteDance interview', now_ms=now_ms)
        self.assertIn('另一个账号的私密面试记录', user2_recalled)
        self.assertNotIn('IELTS', user2_recalled)

        # 5. 向量不可用时的降级模式：命中中英关键词仍可召回，完全不命中关键词时不会乱塞无关高分事件
        fallback_config = config.model_copy(update={'embedding': EmbeddingMemoryConfig(enabled=False)})
        fallback_store = EpisodeMemoryStore(
            self.pool.connection, config=fallback_config, embedder=DeterministicEmbedder(enabled=False),
        )
        kw_hit = fallback_store.recall(1, '还记得我之前拿到的 offer 吗', exclude_from_round=10, now_ms=now_ms)
        self.assertIn('ByteDance 前端面试拿到 offer', kw_hit)
        kw_miss = fallback_store.recall(1, '完全无关的话题测试', exclude_from_round=10, now_ms=now_ms)
        self.assertNotIn('绿萝', kw_miss)
        self.assertNotIn('ByteDance', kw_miss)

        # 6. 清空接口校验
        reset_chat_history(self.pool, user_id=1)
        self.assertEqual(store.list_episodes(1), [])
        self.assertEqual(len(store.list_episodes(2)), 1)
        reset_episode_memories(self.pool)
        self.assertEqual(store.list_episodes(2), [])

    def test_startup_clears_or_retains_episodes_according_to_system_config(self):
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from app.accounts.profile import ProfileStore
        from app.accounts.repository import User
        from app.application import build_application
        from app.llm.admin_settings import SettingsStore
        from app.system_config import SystemConfig

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / 'auth_config'
            legacy.mkdir()
            (root / '.env').write_text("ADMIN_PROVIDERS='{}'\n", encoding='utf-8')
            for clear_flag in (False, True):
                with (
                    self.subTest(clear_episodes_on_startup=clear_flag),
                    patch('app.application.SQLAccountStore') as accounts,
                    patch('app.application.initialize_schema'),
                    patch('app.application.reset_chat_history'),
                    patch('app.application.reset_episode_memories') as clear_episodes,
                    patch('app.application.settings_store', SettingsStore(root / 'settings.json')),
                    patch('app.accounts.preferences.UserPreferencesStore.find', return_value=None),
                ):
                    accounts.return_value.get_admin.return_value = User(1, 'admin', 'admin')
                    cfg = SystemConfig(
                        chat={'clear_history_on_startup': False, 'clear_learned_portrait_on_startup': False},
                        memory={'clear_episodes_on_startup': clear_flag},
                    )
                    app = build_application(MagicMock(), ProfileStore(root / f'users_{clear_flag}'),
                                            legacy, system_config=cfg, prepare_embedding=True)
                    with patch.object(app.state.episodes.embedder, 'prepare_on_startup') as prep_mock:
                        with TestClient(app):
                            pass
                        prep_mock.assert_called_once()
                    if clear_flag:
                        clear_episodes.assert_called_once()
                    else:
                        clear_episodes.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'requires LangGraph')
class EpisodeWorkflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_sessions_extract_episodes_in_background_and_recall_into_prompts(self):
        from app.accounts.repository import User
        from app.chat.scheduler import ChatSessions
        from app.chat.workflow import create_graph

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pool = SQLitePool(Path(folder.name) / 'workflow_episodes.sqlite')
        config = MemoryConfig(
            recent_window_rounds=2,
            extract_batch_rounds=2,
            embedding=EmbeddingMemoryConfig(query_prefix='', passage_prefix='', model_dir='custom'),
        )
        store = EpisodeMemoryStore(pool.connection, config=config, embedder=DeterministicEmbedder())
        sessions = ChatSessions(
            pool.connection,
            episodes=store,
            graph_factory=partial(create_graph, episodes=store, recent_window_rounds=config.recent_window_rounds),
        )
        self.addAsyncCleanup(sessions.drain)
        user = User(1, 'First', 'admin')

        analysis_prompts: list[str] = []
        answer_prompts: list[str] = []

        def analyze(prompt: str) -> str:
            analysis_prompts.append(prompt)
            return '{"intent":"聊天","emotion":"平静","pace":"简短","answer":"自然回应"}'

        def summarize(prompt: str) -> str:
            if '情景事件记忆卡片' in prompt:
                return json.dumps({
                    'episodes': [{
                        'happened_at': '2026-11-26',
                        'summary': '用户在准备 ByteDance 的 React 前端面试',
                        'keywords': ['字节跳动', 'ByteDance', 'React', '面试', 'interview'],
                        'status': 'ongoing',
                        'importance': 5,
                    }],
                    'updates': [],
                }, ensure_ascii=False)
            return 'unused'

        def answer(prompt: str) -> str:
            answer_prompts.append(prompt)
            return '{"reply":["好呀，我都记着呢"]}'

        agents = (FakeAgent(analyze), FakeAgent(summarize), FakeAgent(answer))
        inputs = [
            '我在准备 ByteDance 的 React 面试',
            '最近每天都在刷题',
            '今天天气挺凉快的',
            '晚饭喝了一碗热汤',
        ]
        with patch.object(ChatSessions, '_snapshot_agents', return_value=agents):
            for text in inputs:
                res = await sessions.send(user, text)
                self.assertTrue(res['saved'])
            # 等待后台异步归档协程完成（无需关闭会话池）
            entry = sessions._users[user.id]
            if entry.episode_task is not None:
                await entry.episode_task

            self.assertEqual(store.get_watermark(user.id), 1)
            self.assertEqual(len(store.list_episodes(user.id)), 1)
            # 数据库中的全部 4 轮原文完好保留
            self.assertEqual(len(load_history(pool.connection, user.id, 0)), 4)

            # 第 5 轮用英文提及 interview：短期窗口只带最近 2 轮（轮次 2、3），更早的事件通过长期记忆召回注入提示词
            fifth = await sessions.send(user, 'Any tips for my React interview tomorrow?')
            self.assertTrue(fifth['saved'])

        latest_analysis = analysis_prompts[-1]
        latest_answer = answer_prompts[-1]
        for prompt in (latest_analysis, latest_answer):
            self.assertIn('用户在准备 ByteDance 的 React 前端面试', prompt)
            self.assertIn('今天天气挺凉快的', prompt)
            self.assertIn('晚饭喝了一碗热汤', prompt)
            # 滑出 recent_window_rounds=2 的早期原话不再直接塞入短期对话流
            self.assertNotIn('最近每天都在刷题', prompt)
