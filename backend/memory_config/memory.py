import json
from log_config import logger



def load_history(connection, user_id: int, k: int, front: bool = True):
    """Read committed history for one authenticated account."""
    with connection() as db, db.cursor() as cursor:
        return _read_history(cursor, user_id, k, front)


def _read_history(cursor, user_id: int, k: int, front: bool = True):
    ascending = front if k >= 0 else not front
    direction = 'ASC' if ascending else 'DESC'
    sql = (
        'SELECT conversation_round, user, assistant FROM history_messages '
        f'WHERE user_id = %s ORDER BY conversation_round {direction}'
    )
    params = (user_id,)
    if k != 0:
        sql += ' LIMIT %s'
        params += (max(k, 1),)
    cursor.execute(sql, params)
    return [
        {'conversation_round': row[0], 'user': row[1], 'assistant': json.loads(row[2])}
        for row in cursor.fetchall()
    ]


class MemoryManager:
    """Cache an account's committed chat history and summary."""

    def __init__(self, user_id: int, connection):
        self.user_id = user_id
        self._connection = connection
        self._stale = True
        self.refresh_if_needed()

    def refresh_if_needed(self):
        if not self._stale:
            return
        with self._connection() as db, db.cursor() as cursor:
            history = _read_history(cursor, self.user_id, 0)
            cursor.execute('SELECT abstract FROM abstract_messages WHERE user_id = %s', (self.user_id,))
            row = cursor.fetchone()
        self.history_cache = history
        self.abstract_cache = row[0] if row else ''
        self._stale = False

    def _commit(self, db):
        try:
            db.commit()
        except Exception:
            # A lost response can leave either outcome on the server.
            self._stale = True
            raise

    @property
    def next_round(self) -> int:
        return self.history_cache[-1]['conversation_round'] + 1 if self.history_cache else 0

    def record_message(self, message) -> bool:
        try:
            with self._connection() as db:
                with db.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO history_messages (user_id, conversation_round, user, assistant) '
                        'VALUES (%s, %s, %s, %s)',
                        (self.user_id, message['conversation_round'], message['user'],
                         json.dumps(message['assistant'], ensure_ascii=False)),
                    )
                self._commit(db)
        except Exception as error:
            logger.warning('history write failed: %s', type(error).__name__)
            return False

        self.history_cache.append({**message, 'assistant': message['assistant'].copy()})
        return True

    def compress_history(self, summary_agent):
        from persona_config import persona
        from utils.json_parser import model_text

        count = len(self.history_cache) // 2
        if count == 0:
            return
        older = self.history_cache[:count]
        prompt = f"""请你对用户（对应user）和{persona.BOT_NAME}（对应assistant）的历史对话及更早的历史对话摘要进行缩减，保留重点，返回精确的重点摘要。
历史对话：{older}。
更早的历史对话摘要：{self.abstract_cache}。"""
        try:
            result = summary_agent.invoke({'messages': [{'role': 'user', 'content': prompt}]})
            messages = result.get('messages', [])
            summary = model_text(messages[-1].content) if messages else ''
            if not summary.strip():
                raise ValueError('模型返回的摘要为空')
            with self._connection() as db:
                with db.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO abstract_messages (user_id, abstract) VALUES (%s, %s) '
                        'ON DUPLICATE KEY UPDATE abstract = %s',
                        (self.user_id, summary, summary),
                    )
                    removed = cursor.execute(
                        'DELETE FROM history_messages WHERE user_id = %s AND conversation_round <= %s',
                        (self.user_id, older[-1]['conversation_round']),
                    )
                    if removed != count:
                        raise ValueError('历史对话在压缩时发生变化')
                self._commit(db)
        except Exception as error:
            logger.warning('history compression failed: %s', type(error).__name__)
            return

        self.abstract_cache = summary
        del self.history_cache[:count]
