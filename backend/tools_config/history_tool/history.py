"""Search committed earlier raw chat messages for the current authenticated account."""

import json
import re
from datetime import datetime, time as dt_time
from logging import WARNING
from zoneinfo import ZoneInfo

from app.account_logging import log_event, logger
from app.chat.tool_context import current_tool_context
from app.llm.persona import persona

SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
MAX_KEYWORDS = 4
MAX_KEYWORD_CHARS = 64
MAX_HIT_ROUNDS = 5
MAX_LINE_CHARS = 240
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _parse_date_bound(raw: str, *, end_of_day: bool) -> int | None:
    text = raw.strip()
    if not text:
        return None
    if not _DATE_RE.match(text):
        raise ValueError('invalid_date')
    try:
        parsed_date = datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('invalid_date') from None
    clock = dt_time(23, 59, 59, 999000) if end_of_day else dt_time(0, 0, 0)
    dt = datetime.combine(parsed_date, clock, tzinfo=SHANGHAI_TZ)
    return int(dt.timestamp() * 1000)


def _clean_keywords(keywords: list[str] | tuple[str, ...]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in keywords:
        if not isinstance(item, str):
            continue
        term = item.strip()
        if not term:
            continue
        if len(term) > MAX_KEYWORD_CHARS:
            term = term[:MAX_KEYWORD_CHARS]
        key = term.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(term)
        if len(cleaned) >= MAX_KEYWORDS:
            break
    return cleaned


def _truncate(text: str, limit: int = MAX_LINE_CHARS) -> str:
    compact = re.sub(r'\s+', ' ', text).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit] + '…'


def _format_attachment_tag(attachments: list[dict] | None) -> str:
    if not attachments:
        return ''
    parts = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        label = '图片' if att.get('kind') == 'image' else '文档'
        name = str(att.get('name') or '').strip()
        parts.append(f'[发送了{label}：{name}]' if name else f'[发送了{label}]')
    return ' '.join(parts)


def _format_round(row: dict, bot_name: str) -> str:
    created_at = row.get('created_at')
    if isinstance(created_at, int) and created_at > 0:
        date_str = datetime.fromtimestamp(created_at / 1000.0, tz=SHANGHAI_TZ).strftime('%Y-%m-%d %H:%M')
        header = f'[{date_str}]'
    else:
        header = f"[第 {row['conversation_round'] + 1} 轮]"
    att_tag = _format_attachment_tag(row.get('attachments'))
    user_part = _truncate(row['user'])
    combined_user = f'{att_tag} {user_part}'.strip() if att_tag else user_part
    lines = [f"{header} 对方：{combined_user}"]
    for reply in row['assistant']:
        if isinstance(reply, str) and reply.strip():
            lines.append(f"{bot_name}：{_truncate(reply)}")
    return '\n'.join(lines)


def search_chat_history(keywords: list[str] = (), start_date: str = '', end_date: str = '') -> str:
    logger.info('调用早期对话原文检索工具...')
    terms = _clean_keywords(keywords)
    try:
        start_ms = _parse_date_bound(start_date, end_of_day=False)
        end_ms = _parse_date_bound(end_date, end_of_day=True)
    except ValueError:
        return '日期格式无效，请使用 YYYY-MM-DD（例如 2026-10-15）。'

    if start_ms is not None and end_ms is not None and start_ms > end_ms:
        return '起始日期不能晚于结束日期。'
    if not terms and start_ms is None and end_ms is None:
        return '请提供至少一个搜索关键词或起止日期。'

    ctx = current_tool_context()
    if ctx is None or ctx.connection is None:
        return '[工具提示：当前环境暂无法查询早期聊天记录]'

    conditions = ['user_id = %s']
    params: list[object] = [ctx.user_id]
    if ctx.window_start_round is not None:
        conditions.append('conversation_round < %s')
        params.append(ctx.window_start_round)
    if start_ms is not None:
        conditions.append('created_at >= %s')
        params.append(start_ms)
    if end_ms is not None:
        conditions.append('created_at <= %s')
        params.append(end_ms)

    like_clauses: list[str] = []
    for term in terms:
        # 剥除 SQL 通配符做候选预筛，随后在 Python 中做精确大小写无关子串校验，兼容 MySQL 与 SQLite。
        sql_term = term.replace('%', '').replace('_', '').strip()
        if sql_term:
            pattern = f'%{sql_term}%'
            like_clauses.append('(user LIKE %s OR assistant LIKE %s OR attachments LIKE %s)')
            params.extend((pattern, pattern, pattern))
    if like_clauses:
        conditions.append('(' + ' OR '.join(like_clauses) + ')')

    sql = (
        'SELECT conversation_round, user, assistant, created_at, attachments FROM history_messages '
        f"WHERE {' AND '.join(conditions)} ORDER BY conversation_round DESC LIMIT 80"
    )
    try:
        with ctx.connection() as db, db.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
    except Exception as error:
        log_event('tool_failure', level=WARNING, tool='search_chat_history', error=type(error).__name__)
        return '聊天记录暂时无法查询，请稍后再试。'

    def _row_dict(raw) -> dict:
        try:
            assistant_list = json.loads(raw[2]) if raw[2] else []
            if not isinstance(assistant_list, list):
                assistant_list = []
        except ValueError:
            assistant_list = []
        row_item = {
            'conversation_round': int(raw[0]),
            'user': str(raw[1] or ''),
            'assistant': [str(part) for part in assistant_list if isinstance(part, str)],
            'created_at': int(raw[3]) if raw[3] is not None else None,
        }
        if len(raw) > 4 and raw[4]:
            try:
                parsed_att = json.loads(raw[4])
                if isinstance(parsed_att, list) and parsed_att:
                    row_item['attachments'] = parsed_att
            except ValueError:
                pass
        return row_item

    scored_hits: list[tuple[int, int, dict]] = []
    lower_terms = [t.lower() for t in terms]
    for raw in raw_rows:
        item = _row_dict(raw)
        if lower_terms:
            att_tag = _format_attachment_tag(item.get('attachments'))
            combined = (att_tag + '\n' + item['user'] + '\n' + '\n'.join(item['assistant'])).lower()
            hits = sum(1 for term in lower_terms if term in combined)
            if hits == 0:
                continue
        else:
            hits = 1
        scored_hits.append((hits, item['conversation_round'], item))

    if not scored_hits:
        return '未在早期对话记录中找到匹配的原话。'

    scored_hits.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    top_rounds = [entry[1] for entry in scored_hits[:MAX_HIT_ROUNDS]]

    # 补全命中轮次前后相邻 1 轮，避免店名/书名出现在上一句或下一句而漏掉语境。
    expanded_rounds: set[int] = set()
    upper_bound = ctx.window_start_round
    for r in top_rounds:
        for neighbor in (r - 1, r, r + 1):
            if neighbor >= 0 and (upper_bound is None or neighbor < upper_bound):
                expanded_rounds.add(neighbor)

    by_round = {entry[1]: entry[2] for entry in scored_hits}
    missing = sorted(r for r in expanded_rounds if r not in by_round)
    if missing:
        placeholders = ', '.join(['%s'] * len(missing))
        neighbor_sql = (
            'SELECT conversation_round, user, assistant, created_at, attachments FROM history_messages '
            f'WHERE user_id = %s AND conversation_round IN ({placeholders})'
        )
        try:
            with ctx.connection() as db, db.cursor() as cursor:
                cursor.execute(neighbor_sql, (ctx.user_id, *missing))
                for raw in cursor.fetchall():
                    parsed = _row_dict(raw)
                    by_round[parsed['conversation_round']] = parsed
        except Exception:
            pass

    ordered_rounds = sorted(r for r in expanded_rounds if r in by_round)
    groups: list[list[dict]] = []
    for r in ordered_rounds:
        if groups and r == groups[-1][-1]['conversation_round'] + 1:
            groups[-1].append(by_round[r])
        else:
            groups.append([by_round[r]])

    bot_name = persona.BOT_NAME
    blocks = ['\n'.join(_format_round(row, bot_name) for row in group) for group in groups]
    return '找到以下早期对话片段：\n\n' + '\n\n---\n\n'.join(blocks)
