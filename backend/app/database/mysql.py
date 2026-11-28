"""创建数据库、迁移表结构，并在应用启动时按配置清理聊天与记忆数据。"""
import re

import pymysql

from app.system_config import DatabaseConfig, load_system_config
from .pool import ConnectionPool


def open_pool(database=None, *, config: DatabaseConfig | None = None, create_if_missing=True, **overrides):
    config = config or load_system_config().database
    database = config.name if database is None else database
    if not re.fullmatch(r'[A-Za-z0-9_]+', database):
        raise ValueError('数据库名称无效')
    options = {
        'host': config.host,
        'user': config.user,
        'password': config.password.get_secret_value(),
        'port': config.port,
        'charset': config.charset,
        'connect_timeout': 5,
        'read_timeout': 15,
        'write_timeout': 15,
        **overrides,
    }
    # 连接池按需建连接，只有允许初始化时才补建缺失的数据库。
    def connect():
        try:
            return pymysql.connect(database=database, **options)
        except pymysql.OperationalError as error:
            if error.args[0] != 1049 or not create_if_missing:
                raise
        connection = pymysql.connect(**options)
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        finally:
            connection.close()
        return pymysql.connect(database=database, **options)

    return ConnectionPool(connect)


def initialize_schema(pool):
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(32) NOT NULL,
                username_key VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(16) NOT NULL,
                uses_username_directory BOOLEAN NOT NULL DEFAULT FALSE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        cursor.execute("SHOW COLUMNS FROM users LIKE 'uses_username_directory'")
        if cursor.fetchone() is None:
            cursor.execute('ALTER TABLE users ADD COLUMN uses_username_directory BOOLEAN NOT NULL DEFAULT FALSE')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id BIGINT UNSIGNED PRIMARY KEY,
                send_shortcut VARCHAR(16) NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history_messages (
                user_id BIGINT UNSIGNED NOT NULL,
                conversation_round INT NOT NULL,
                user MEDIUMTEXT,
                assistant MEDIUMTEXT,
                created_at BIGINT NULL,
                attachments TEXT NULL,
                PRIMARY KEY (user_id, conversation_round)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        cursor.execute("SHOW COLUMNS FROM history_messages LIKE 'user_id'")
        if cursor.fetchone() is None:
            # 单用户历史本来就在重启时清空，迁移后按账号重新开始编号。
            cursor.execute('DELETE FROM history_messages')
            cursor.execute('''
                ALTER TABLE history_messages
                ADD COLUMN user_id BIGINT UNSIGNED NOT NULL FIRST,
                DROP PRIMARY KEY,
                ADD PRIMARY KEY (user_id, conversation_round)
            ''')
        cursor.execute("SHOW COLUMNS FROM history_messages LIKE 'created_at'")
        if cursor.fetchone() is None:
            cursor.execute('ALTER TABLE history_messages ADD COLUMN created_at BIGINT NULL')
        cursor.execute("SHOW COLUMNS FROM history_messages LIKE 'attachments'")
        if cursor.fetchone() is None:
            cursor.execute('ALTER TABLE history_messages ADD COLUMN attachments TEXT NULL')
        cursor.execute("SHOW INDEX FROM history_messages WHERE Key_name = 'idx_history_messages_user_created'")
        if cursor.fetchone() is None:
            cursor.execute('ALTER TABLE history_messages ADD INDEX idx_history_messages_user_created (user_id, created_at)')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS abstract_messages (
                user_id BIGINT UNSIGNED PRIMARY KEY,
                abstract MEDIUMTEXT NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        cursor.execute("SHOW COLUMNS FROM abstract_messages LIKE 'user_id'")
        if cursor.fetchone() is None:
            cursor.execute('DELETE FROM abstract_messages')
            cursor.execute('''
                ALTER TABLE abstract_messages
                ADD COLUMN user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY FIRST
            ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_episodes (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT UNSIGNED NOT NULL,
                happened_at VARCHAR(32) NOT NULL,
                summary TEXT NOT NULL,
                keywords TEXT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'fact',
                importance TINYINT NOT NULL DEFAULT 3,
                source_round_start INT NULL,
                source_round_end INT NULL,
                embedding MEDIUMBLOB NULL,
                embedding_dim SMALLINT NULL,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                INDEX idx_memory_episodes_user_status (user_id, status, updated_at),
                INDEX idx_memory_episodes_user_round (user_id, source_round_end)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_watermarks (
                user_id BIGINT UNSIGNED PRIMARY KEY,
                last_extracted_round INT NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        # 单用户版本继承服务器的默认引擎，压缩和清理需要所有聊天与记忆表都支持事务。
        cursor.execute('''
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = DATABASE() AND engine <> 'InnoDB'
              AND table_name IN ('history_messages', 'abstract_messages', 'memory_episodes', 'memory_watermarks')
        ''')
        legacy_engines = {table for (table,) in cursor.fetchall()}
        for table in ('history_messages', 'abstract_messages', 'memory_episodes', 'memory_watermarks'):
            if table in legacy_engines:
                cursor.execute(f'ALTER TABLE {table} ENGINE=InnoDB')
        connection.commit()


def delete_user_history(cursor, user_id: int) -> None:
    cursor.execute('DELETE FROM history_messages WHERE user_id = %s', (user_id,))
    cursor.execute('DELETE FROM abstract_messages WHERE user_id = %s', (user_id,))
    cursor.execute('DELETE FROM memory_episodes WHERE user_id = %s', (user_id,))
    cursor.execute('DELETE FROM memory_watermarks WHERE user_id = %s', (user_id,))


def reset_chat_history(pool, user_id: int | None = None):
    # 清空聊天原文、旧摘要及归档水位；单账号清空时同步清空该账号事件记忆。
    with pool.connection() as connection, connection.cursor() as cursor:
        if user_id is None:
            cursor.execute('DELETE FROM history_messages')
            cursor.execute('DELETE FROM abstract_messages')
            cursor.execute('DELETE FROM memory_watermarks')
        else:
            delete_user_history(cursor, user_id)
        connection.commit()


def reset_episode_memories(pool, user_id: int | None = None):
    with pool.connection() as connection, connection.cursor() as cursor:
        if user_id is None:
            cursor.execute('DELETE FROM memory_episodes')
            cursor.execute('DELETE FROM memory_watermarks')
        else:
            cursor.execute('DELETE FROM memory_episodes WHERE user_id = %s', (user_id,))
            cursor.execute('DELETE FROM memory_watermarks WHERE user_id = %s', (user_id,))
        connection.commit()
