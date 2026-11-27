import json
from logging import WARNING

from app.account_logging import log_event, logger


HISTORY_CACHE_LIMIT = 256
HISTORY_COMPRESSION_BATCH_SIZE = 32


def _row_to_item(row) -> dict:
    item = {'conversation_round': row[0], 'user': row[1], 'assistant': json.loads(row[2])}
    if len(row) > 3 and row[3] is not None:
        item['created_at'] = int(row[3])
    return item


def prompt_history(rows: list[dict]) -> list[dict]:
    """Strip UI metadata such as timestamps before interpolating history into prompts."""
    return [
        {'conversation_round': row['conversation_round'], 'user': row['user'], 'assistant': row['assistant']}
        for row in rows
    ]


def format_history_dialogue(rows: list[dict], *, bot_name: str = '小晚') -> str:
    """Render committed history as natural chat lines instead of Python dict repr."""
    if not rows:
        return '无'
    blocks = []
    for row in rows:
        lines = [f"对方：{row['user']}"]
        lines.extend(f"{bot_name}：{part}" for part in row['assistant'])
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks)


def load_history(connection, user_id: int, k: int, front: bool = True):
    """Read committed history for one authenticated account."""
    with connection() as db, db.cursor() as cursor:
        return _read_history(cursor, user_id, k, front)


def _read_history(cursor, user_id: int, k: int, front: bool = True):
    ascending = front if k >= 0 else not front
    direction = 'ASC' if ascending else 'DESC'
    sql = (
        'SELECT conversation_round, user, assistant, created_at FROM history_messages '
        f'WHERE user_id = %s ORDER BY conversation_round {direction}'
    )
    params = (user_id,)
    if k != 0:
        sql += ' LIMIT %s'
        params += (abs(k),)
    cursor.execute(sql, params)
    return [_row_to_item(row) for row in cursor.fetchall()]


class MemoryManager:
    """Cache an account's committed chat history and summary."""

    def __init__(self, user_id: int, connection):
        self.user_id = user_id
        self._connection = connection
        self._stale = True
        self.refresh_if_needed()

    def _read_recent_history(self, cursor):
        newest_first = _read_history(cursor, self.user_id, HISTORY_CACHE_LIMIT + 1, front=False)
        has_older_history = len(newest_first) > HISTORY_CACHE_LIMIT
        return list(reversed(newest_first[:HISTORY_CACHE_LIMIT])), has_older_history

    def refresh_if_needed(self):
        if not self._stale:
            return
        with self._connection() as db, db.cursor() as cursor:
            history, has_older_history = self._read_recent_history(cursor)
            cursor.execute('SELECT abstract FROM abstract_messages WHERE user_id = %s', (self.user_id,))
            row = cursor.fetchone()
        self.history_cache = history
        self.has_older_history = has_older_history
        self.abstract_cache = row[0] if row else ''
        self._stale = False

    def _commit(self, db):
        try:
            db.commit()
        except Exception:
            # 提交响应丢失时，数据库可能已经完成提交，下次读取前先刷新缓存。
            self._stale = True
            raise

    @property
    def next_round(self) -> int:
        return self.history_cache[-1]['conversation_round'] + 1 if self.history_cache else 0

    @staticmethod
    def _cached_item(message) -> dict:
        item = {
            'conversation_round': message['conversation_round'],
            'user': message['user'],
            'assistant': message['assistant'].copy(),
        }
        if message.get('created_at') is not None:
            item['created_at'] = int(message['created_at'])
        return item

    def record_message(self, message) -> bool:
        created_at = message.get('created_at')
        try:
            with self._connection() as db:
                with db.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO history_messages (user_id, conversation_round, user, assistant, created_at) '
                        'VALUES (%s, %s, %s, %s, %s)',
                        (self.user_id, message['conversation_round'], message['user'],
                         json.dumps(message['assistant'], ensure_ascii=False), created_at),
                    )
                self._commit(db)
        except Exception as error:
            logger.warning('history write failed: %s', type(error).__name__)
            return False

        # 只把提交成功的轮次放进记忆，模型不会参考已知保存失败的消息。
        self.history_cache.append(self._cached_item(message))
        if len(self.history_cache) > HISTORY_CACHE_LIMIT:
            del self.history_cache[0]
            self.has_older_history = True
        return True

    def compress_history(self, summary_agent):
        from app.llm.persona import persona
        from app.llm.output_parser import model_text

        # 从数据库读取最早的记录，缓存之外的旧对话也会被摘要。
        count = min(len(self.history_cache) // 2, HISTORY_COMPRESSION_BATCH_SIZE)
        if count == 0:
            return
        try:
            with self._connection() as db, db.cursor() as cursor:
                older = _read_history(cursor, self.user_id, count)
            if len(older) != count:
                self._stale = True
                log_event('history_compression', level=WARNING, outcome='skipped', reason='history_changed',
                          expected_rounds=count, available_rounds=len(older))
                return

            prompt = f"""【已有摘要】
{self.abstract_cache or '无'}

【这次归入摘要的对话，按时间顺序】
{format_history_dialogue(older, bot_name=persona.BOT_NAME)}

请合并已有摘要和这批对话，输出完整的更新摘要。两部分都没有值得保留的信息时，写「暂无需要延续的对话信息。」"""
            result = summary_agent.invoke({'messages': [{'role': 'user', 'content': prompt}]})
            messages = result.get('messages', [])
            summary = model_text(messages[-1].content) if messages else ''
            if not summary.strip():
                raise ValueError('模型返回的摘要为空')
            cutoff = older[-1]['conversation_round']
            history = [row for row in self.history_cache if row['conversation_round'] > cutoff]
            has_older_history = self.has_older_history
            with self._connection() as db:
                with db.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO abstract_messages (user_id, abstract) VALUES (%s, %s) '
                        'ON DUPLICATE KEY UPDATE abstract = %s',
                        (self.user_id, summary, summary),
                    )
                    # 摘要写入与旧原文删除同事务提交，受影响范围始终绑定当前账号。
                    removed = cursor.execute(
                        'DELETE FROM history_messages WHERE user_id = %s AND conversation_round <= %s',
                        (self.user_id, cutoff),
                    )
                    if removed != count:
                        self._stale = True
                        raise ValueError('历史对话在压缩时发生变化')
                    if has_older_history:
                        history, has_older_history = self._read_recent_history(cursor)
                self._commit(db)
        except Exception as error:
            log_event('history_compression', level=WARNING, outcome='failed', error=type(error).__name__,
                      attempted_rounds=count)
            return

        self.abstract_cache = summary
        self.history_cache = history
        self.has_older_history = has_older_history
        log_event('history_compression', outcome='succeeded', rounds=removed, through_round=cutoff)
