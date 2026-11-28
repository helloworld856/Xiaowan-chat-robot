"""Structured episodic long-term memory with local ONNX vector + bilingual keyword hybrid recall."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from logging import WARNING
from threading import Event
from time import time

import numpy as np

from app.account_logging import log_event
from app.chat.memory import format_history_dialogue
from app.llm.embeddings import LocalEmbeddingModel, cosine_similarity, pack_vector, unpack_vector
from app.llm.output_parser import model_text, parse_model_json
from app.llm.persona import persona
from app.system_config import MemoryConfig

VALID_STATUSES = frozenset(('ongoing', 'resolved', 'fact'))
_WORD_RE = re.compile(r'[a-z0-9+#._-]{2,}')
_CJK_RE = re.compile(r'[\u4e00-\u9fff]{2,}')
_CJK_CHAR_RE = re.compile(r'[\u4e00-\u9fff]')


@dataclass
class EpisodeRecord:
    id: int
    happened_at: str
    summary: str
    keywords: list[str]
    status: str
    importance: int
    source_round_start: int | None
    source_round_end: int | None
    embedding: np.ndarray | None
    created_at: int
    updated_at: int


# 提取英文/数字词元与中文双字切分（bigram），无需额外分词库即可兼顾中英专有名词匹配。
def _extract_tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(_WORD_RE.findall(lowered))
    for chunk in _CJK_RE.findall(lowered):
        if len(chunk) == 2:
            tokens.add(chunk)
        else:
            for idx in range(len(chunk) - 1):
                tokens.add(chunk[idx:idx + 2])
    return tokens


def _keyword_overlap_score(query_text: str, query_tokens: set[str], episode: EpisodeRecord) -> float:
    if not query_text:
        return 0.0
    lowered_query = query_text.lower()
    direct_hits = 0
    for kw in episode.keywords:
        norm_kw = kw.strip().lower()
        if len(norm_kw) >= 2 and norm_kw in lowered_query:
            direct_hits += 1
    if direct_hits >= 2:
        return 1.0

    ep_tokens = _extract_tokens(' '.join(episode.keywords) + ' ' + episode.summary)
    overlap = len(query_tokens & ep_tokens) / max(1, min(len(query_tokens), 8)) if query_tokens and ep_tokens else 0.0
    if direct_hits == 1:
        return max(0.65, min(1.0, overlap + 0.45))
    return min(1.0, overlap)


def _calibrate_semantic(raw_cosine: float, *, uses_e5_prefix: bool, cross_script: bool) -> float:
    if uses_e5_prefix:
        # multilingual-e5-small 同语种无关句余弦约在 0.80~0.85，中英跨语种无关句约在 0.72~0.75；
        # 按是否跨语种分别扣除各向异性基线后再线性拉伸到 [0, 1]，避免日常闲聊误召回无关卡片。
        baseline = 0.74 if cross_script else 0.83
        span = 0.14
        return max(0.0, min(1.0, (raw_cosine - baseline) / span))
    return max(0.0, min(1.0, raw_cosine))


def _format_episode_line(episode: EpisodeRecord) -> str:
    status_tag = ' · 进行中' if episode.status == 'ongoing' else ''
    return f'- [{episode.happened_at}{status_tag}] {episode.summary}'


class EpisodeMemoryStore:
    def __init__(self, connection, *, config: MemoryConfig | None = None,
                 embedder: LocalEmbeddingModel | None = None):
        self._connection = connection
        self.config = config or MemoryConfig()
        self.embedder = embedder or LocalEmbeddingModel(self.config.embedding)

    def _uses_e5_prefix(self) -> bool:
        prefix = self.config.embedding.query_prefix.strip().lower()
        return prefix.startswith('query:') or 'e5' in self.config.embedding.model_dir.lower()

    def get_watermark(self, user_id: int) -> int:
        with self._connection() as db, db.cursor() as cursor:
            cursor.execute('SELECT last_extracted_round FROM memory_watermarks WHERE user_id = %s', (user_id,))
            row = cursor.fetchone()
        return int(row[0]) if row is not None else -1

    def list_episodes(self, user_id: int) -> list[EpisodeRecord]:
        with self._connection() as db, db.cursor() as cursor:
            cursor.execute(
                'SELECT id, happened_at, summary, keywords, status, importance, '
                'source_round_start, source_round_end, embedding, embedding_dim, created_at, updated_at '
                'FROM memory_episodes WHERE user_id = %s ORDER BY updated_at DESC, id DESC',
                (user_id,),
            )
            rows = cursor.fetchall()
        records: list[EpisodeRecord] = []
        for row in rows:
            try:
                keywords = json.loads(row[3]) if row[3] else []
                if not isinstance(keywords, list):
                    keywords = []
            except ValueError:
                keywords = []
            records.append(EpisodeRecord(
                id=int(row[0]),
                happened_at=str(row[1]),
                summary=str(row[2]),
                keywords=[str(k) for k in keywords if isinstance(k, str)],
                status=str(row[4]) if row[4] in VALID_STATUSES else 'fact',
                importance=max(1, min(5, int(row[5] or 3))),
                source_round_start=int(row[6]) if row[6] is not None else None,
                source_round_end=int(row[7]) if row[7] is not None else None,
                embedding=unpack_vector(row[8], int(row[9]) if row[9] is not None else None),
                created_at=int(row[10]),
                updated_at=int(row[11]),
            ))
        return records

    def recall(self, user_id: int, query_text: str, *, exclude_from_round: int | None = None,
               now_ms: int | None = None) -> str:
        """Two-way hybrid recall: recent ongoing episodes + relevant historical episodes."""
        ongoing_limit = self.config.ongoing_recall_limit
        relevant_limit = self.config.relevant_recall_limit
        if ongoing_limit <= 0 and relevant_limit <= 0:
            return '无'

        episodes = self.list_episodes(user_id)
        if not episodes:
            return '无'

        if exclude_from_round is not None:
            # 仍在短期原话窗口内的非 ongoing 记录无需重复塞入长期记忆；ongoing 事件保留以便提示状态。
            episodes = [
                ep for ep in episodes
                if ep.source_round_end is None or ep.source_round_end < exclude_from_round or ep.status == 'ongoing'
            ]
        if not episodes:
            return '无'

        now = now_ms if now_ms is not None else int(time() * 1000)
        selected: list[EpisodeRecord] = []
        selected_ids: set[int] = set()

        # 第一路：近期仍处于 ongoing 的事件（优先重要度与更新时间）
        if ongoing_limit > 0:
            cutoff_ms = now - self.config.ongoing_window_days * 86_400_000
            ongoing_candidates = [
                ep for ep in episodes
                if ep.status == 'ongoing' and ep.updated_at >= cutoff_ms
            ]
            ongoing_candidates.sort(key=lambda ep: (ep.importance, ep.updated_at), reverse=True)
            for ep in ongoing_candidates[:ongoing_limit]:
                selected.append(ep)
                selected_ids.add(ep.id)

        # 第二路：向量语义 + 中英关键词 + 时间/重要度 混合打分召回
        remaining = [ep for ep in episodes if ep.id not in selected_ids]
        if relevant_limit > 0 and remaining and query_text.strip():
            query_vec = self.embedder.encode_query(query_text) if self.config.embedding.enabled else None
            uses_e5 = self._uses_e5_prefix()
            query_has_cjk = bool(_CJK_CHAR_RE.search(query_text))
            query_tokens = _extract_tokens(query_text)

            w_sem = self.config.embedding.semantic_weight
            w_kw = self.config.embedding.keyword_weight
            w_rec = self.config.embedding.recency_weight

            scored: list[tuple[float, EpisodeRecord]] = []
            for ep in remaining:
                kw_score = _keyword_overlap_score(query_text, query_tokens, ep)
                age_days = max(0.0, (now - ep.updated_at) / 86_400_000)
                recency_decay = 1.0 / (1.0 + age_days / 30.0)
                importance_norm = (ep.importance - 1) / 4.0
                rec_score = 0.5 * recency_decay + 0.5 * importance_norm

                if query_vec is not None and ep.embedding is not None and ep.embedding.shape == query_vec.shape:
                    ep_has_cjk = bool(_CJK_CHAR_RE.search(ep.summary))
                    sem_score = _calibrate_semantic(
                        cosine_similarity(query_vec, ep.embedding),
                        uses_e5_prefix=uses_e5,
                        cross_script=(query_has_cjk != ep_has_cjk),
                    )
                    total_w = w_sem + w_kw + w_rec
                    score = (w_sem * sem_score + w_kw * kw_score + w_rec * rec_score) / total_w
                    # 语义和关键词都偏弱时直接过滤，防止仅靠时间新或重要度高混入无关事件。
                    if sem_score < 0.35 and kw_score < 0.2:
                        continue
                else:
                    # 降级模式：无可用向量时必须至少命中一定关键词，不能仅凭时间新就混入无关记忆。
                    if kw_score <= 0.0:
                        continue
                    fallback_w = (w_kw + w_rec) or 1.0
                    score = (w_kw * kw_score + w_rec * rec_score) / fallback_w

                if score >= self.config.min_relevance_score:
                    scored.append((score, ep))

            scored.sort(key=lambda pair: pair[0], reverse=True)
            for _, ep in scored[:relevant_limit]:
                selected.append(ep)
                selected_ids.add(ep.id)

        if not selected:
            return '无'
        return '\n'.join(_format_episode_line(ep) for ep in selected)

    def unextracted_batch(self, user_id: int, max_eligible_round: int) -> list[dict]:
        """Read up to extract_batch_rounds unarchived turns at or below max_eligible_round."""
        if max_eligible_round < 0:
            return []
        watermark = self.get_watermark(user_id)
        if max_eligible_round - watermark < self.config.extract_batch_rounds:
            return []
        with self._connection() as db, db.cursor() as cursor:
            cursor.execute(
                'SELECT conversation_round, user, assistant, created_at, attachments FROM history_messages '
                'WHERE user_id = %s AND conversation_round > %s AND conversation_round <= %s '
                'ORDER BY conversation_round ASC LIMIT %s',
                (user_id, watermark, max_eligible_round, self.config.extract_batch_rounds),
            )
            rows = cursor.fetchall()
        if len(rows) < self.config.extract_batch_rounds:
            return []
        result = []
        for row in rows:
            item = {
                'conversation_round': int(row[0]),
                'user': row[1],
                'assistant': json.loads(row[2]),
                **({'created_at': int(row[3])} if row[3] is not None else {}),
            }
            if len(row) > 4 and row[4]:
                try:
                    parsed_att = json.loads(row[4])
                    if isinstance(parsed_att, list) and parsed_att:
                        item['attachments'] = parsed_att
                except ValueError:
                    pass
            result.append(item)
        return result

    def extract_and_save_batch(self, user_id: int, summary_agent, max_eligible_round: int, *,
                               cancel_event: Event | None = None) -> bool:
        """提炼一批已滑出短期窗口的对话并推进水位（不删原文）；水位成功推进时返回 True。"""
        if cancel_event is not None and cancel_event.is_set():
            return False
        batch = self.unextracted_batch(user_id, max_eligible_round)
        if not batch:
            return False

        round_start = batch[0]['conversation_round']
        round_end = batch[-1]['conversation_round']
        first_ts = batch[0].get('created_at')
        default_date = datetime.fromtimestamp(first_ts / 1000).strftime('%Y-%m-%d') if first_ts else datetime.now().strftime('%Y-%m-%d')

        existing_ongoing = [ep for ep in self.list_episodes(user_id) if ep.status == 'ongoing'][:15]
        ongoing_context = '\n'.join(
            f'- id={ep.id} [{ep.happened_at}] {ep.summary}' for ep in existing_ongoing
        ) or '无'

        dialogue_text = format_history_dialogue(batch, bot_name=persona.BOT_NAME)
        prompt = f"""你在替{persona.BOT_NAME}从刚滑出短期窗口的一批对话里提炼「情景事件记忆卡片」。

【目前仍处于进行中的旧事件】
{ongoing_context}

【本批对话（参考发生日期：{default_date}）】
{dialogue_text}

请按以下规则提炼：
1. 只记录具体发生的事、计划/约定、重要决定、阶段性烦恼或需要后续记挂的进展；纯寒暄、打招呼、表情、日常废话不要记（可返回空列表）。
2. 已归入「用户画像」的长期静态属性（如固定称呼、常住城市）不必重复记，除非是一次具体事件。
3. summary 用 1 到 2 句自然中文写清事实，保留原文关键英文词（如 offer、React、IELTS）。
4. keywords 给 2 到 6 个检索关键词，**同时包含中文核心词与对应的英文检索词**（即使原文全中文，也补 1 到 2 个对应英文词），方便中英跨语言检索。
5. status 取值："ongoing"（尚未结束、后续值得记挂）、"resolved"（已出结果/已结束）或 "fact"（一般过往事件）。
6. importance 取 1 到 5 的整数（5 最重要）。
7. 如果本批对话让上面某条「进行中的旧事件」有了结果或已结束，在 updates 里给出它的 id、更新后的 summary 和 status（通常为 "resolved"），不要重复新建同一件事。

只输出标准 JSON 对象，格式如下：
{{"episodes":[{{"happened_at":"{default_date}","summary":"…","keywords":["中文词","english"],"status":"ongoing","importance":4}}],"updates":[{{"id":1,"summary":"更新后的完整简述","status":"resolved"}}]}}"""

        try:
            result = summary_agent.invoke({'messages': [{'role': 'user', 'content': prompt}]})
            messages = result.get('messages', [])
            text = model_text(messages[-1].content) if messages else ''
            parsed = parse_model_json(text, ('episodes',))
            if not isinstance(parsed, dict):
                raise ValueError('事件提炼输出不是有效 JSON 对象')
        except Exception as error:
            log_event('episode_extraction', level=WARNING, outcome='failed', error=type(error).__name__,
                      rounds=len(batch))
            return False

        if cancel_event is not None and cancel_event.is_set():
            return False

        raw_episodes = parsed.get('episodes') if isinstance(parsed.get('episodes'), list) else []
        raw_updates = parsed.get('updates') if isinstance(parsed.get('updates'), list) else []

        clean_episodes: list[dict] = []
        for item in raw_episodes[:10]:
            if not isinstance(item, dict):
                continue
            summary = str(item.get('summary') or '').strip()
            if not summary:
                continue
            happened_at = str(item.get('happened_at') or default_date).strip()[:32] or default_date
            raw_kw = item.get('keywords')
            keywords = [str(k).strip()[:32] for k in raw_kw if isinstance(k, str) and str(k).strip()][:8] if isinstance(raw_kw, list) else []
            status = str(item.get('status') or 'fact').strip()
            if status not in VALID_STATUSES:
                status = 'fact'
            try:
                importance = max(1, min(5, int(item.get('importance', 3))))
            except (TypeError, ValueError):
                importance = 3
            clean_episodes.append({
                'happened_at': happened_at,
                'summary': summary[:500],
                'keywords': keywords,
                'status': status,
                'importance': importance,
            })

        ongoing_ids = {ep.id for ep in existing_ongoing}
        clean_updates: list[dict] = []
        for upd in raw_updates[:10]:
            if not isinstance(upd, dict):
                continue
            try:
                target_id = int(upd.get('id'))
            except (TypeError, ValueError):
                continue
            if target_id not in ongoing_ids:
                continue
            new_status = str(upd.get('status') or 'resolved').strip()
            if new_status not in VALID_STATUSES:
                new_status = 'resolved'
            new_summary = str(upd.get('summary') or '').strip()[:500]
            clean_updates.append({'id': target_id, 'status': new_status, 'summary': new_summary})

        # 批量计算新卡片和被更新卡片的向量
        texts_to_embed = [f"{ep['summary']} {' '.join(ep['keywords'])}" for ep in clean_episodes]
        vectors = self.embedder.encode_passages(texts_to_embed) if texts_to_embed else []

        update_texts = [upd['summary'] for upd in clean_updates if upd['summary']]
        update_vectors = self.embedder.encode_passages(update_texts) if update_texts else []

        if cancel_event is not None and cancel_event.is_set():
            return False
        now_ms = int(time() * 1000)
        try:
            with self._connection() as db:
                with db.cursor() as cursor:
                    vec_idx = 0
                    for upd in clean_updates:
                        if upd['summary']:
                            uvec = update_vectors[vec_idx] if update_vectors and vec_idx < len(update_vectors) else None
                            vec_idx += 1
                            if uvec is not None:
                                blob, dim = pack_vector(uvec)
                                cursor.execute(
                                    'UPDATE memory_episodes SET summary = %s, status = %s, embedding = %s, '
                                    'embedding_dim = %s, updated_at = %s WHERE id = %s AND user_id = %s',
                                    (upd['summary'], upd['status'], blob, dim, now_ms, upd['id'], user_id),
                                )
                            else:
                                cursor.execute(
                                    'UPDATE memory_episodes SET summary = %s, status = %s, updated_at = %s '
                                    'WHERE id = %s AND user_id = %s',
                                    (upd['summary'], upd['status'], now_ms, upd['id'], user_id),
                                )
                        else:
                            cursor.execute(
                                'UPDATE memory_episodes SET status = %s, updated_at = %s WHERE id = %s AND user_id = %s',
                                (upd['status'], now_ms, upd['id'], user_id),
                            )

                    for idx, ep in enumerate(clean_episodes):
                        vec = vectors[idx] if vectors and idx < len(vectors) else None
                        blob, dim = pack_vector(vec) if vec is not None else (None, None)
                        cursor.execute(
                            'INSERT INTO memory_episodes (user_id, happened_at, summary, keywords, status, importance, '
                            'source_round_start, source_round_end, embedding, embedding_dim, created_at, updated_at) '
                            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                            (user_id, ep['happened_at'], ep['summary'], json.dumps(ep['keywords'], ensure_ascii=False),
                             ep['status'], ep['importance'], round_start, round_end, blob, dim, now_ms, now_ms),
                        )

                    # 推进归档水位但不删除 history_messages 原文；整批纯寒暄无新卡片时也会推进水位避免重复扫描。
                    cursor.execute(
                        'INSERT INTO memory_watermarks (user_id, last_extracted_round) VALUES (%s, %s) '
                        'ON DUPLICATE KEY UPDATE last_extracted_round = %s',
                        (user_id, round_end, round_end),
                    )
                    self._prune_excess(cursor, user_id)
                if cancel_event is not None and cancel_event.is_set():
                    return False
                db.commit()
        except Exception as error:
            log_event('episode_extraction', level=WARNING, outcome='db_failed', error=type(error).__name__)
            return False

        log_event('episode_extraction', outcome='succeeded', added=len(clean_episodes),
                  updated=len(clean_updates), through_round=round_end)
        return True

    def _prune_excess(self, cursor, user_id: int) -> None:
        cursor.execute('SELECT COUNT(*) FROM memory_episodes WHERE user_id = %s', (user_id,))
        total = int(cursor.fetchone()[0])
        excess = total - self.config.max_episodes_per_account
        if excess <= 0:
            return
        # 优先淘汰已完结/一般事实、重要度低、时间最早的记录
        cursor.execute(
            "SELECT id FROM memory_episodes WHERE user_id = %s "
            "ORDER BY CASE WHEN status = 'ongoing' THEN 1 ELSE 0 END ASC, importance ASC, updated_at ASC LIMIT %s",
            (user_id, excess),
        )
        stale_ids = [int(row[0]) for row in cursor.fetchall()]
        for stale_id in stale_ids:
            cursor.execute('DELETE FROM memory_episodes WHERE id = %s AND user_id = %s', (stale_id, user_id))
