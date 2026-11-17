"""创建数据库、迁移表结构，并在应用启动时清理本次运行的聊天数据。"""
import os
import re

import pymysql

from app.config import configer
from .pool import ConnectionPool


def open_pool(database='XiaoWan', *, create_if_missing=True, **overrides):
    if not re.fullmatch(r'[A-Za-z0-9_]+', database):
        raise ValueError('数据库名称无效')
    options = {
        'host': configer.host,
        'user': configer.user,
        'password': os.getenv('PASSWORD'),
        'port': configer.port,
        'charset': configer.charset,
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
                role VARCHAR(16) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
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
        # 单用户版本继承服务器的默认引擎，压缩和清理需要两个表都支持事务。
        cursor.execute('''
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = DATABASE() AND engine <> 'InnoDB'
              AND table_name IN ('history_messages', 'abstract_messages')
        ''')
        legacy_engines = {table for (table,) in cursor.fetchall()}
        for table in ('history_messages', 'abstract_messages'):
            if table in legacy_engines:
                cursor.execute(f'ALTER TABLE {table} ENGINE=InnoDB')
        connection.commit()


# 启动时同时清空原文与摘要，账号表和个人配置保留。
def reset_chat_history(pool):
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM history_messages')
        cursor.execute('DELETE FROM abstract_messages')
        connection.commit()
