import os

import pymysql

from config import configer
from log_config import logger


def rollback_db(db):
    """尝试回滚，连接失效时记录错误，避免覆盖原来的异常。"""
    try:
        db.rollback()
    except Exception as error:
        logger.warning(f'回滚数据库操作失败:{error}')


def init_db():
    password = os.getenv('PASSWORD')
    connection_config = {
        'host': configer.host,
        'user': configer.user,
        'password': password,
        'port': configer.port,
        'charset': configer.charset,
    }
    # 连接数据库
    try:
        logger.info('连接数据库中...')
        db = pymysql.connect(database='XiaoWan', **connection_config)

        logger.info('数据库连接成功!')
    except pymysql.MySQLError as e:
        if e.args[0] == 1049:   # unknown database
            # 创建数据库
            logger.info('数据库不存在，创建数据库...')
            db = pymysql.connect(**connection_config)

            try:
                with db.cursor() as cursor:
                    sql = "create database if not exists XiaoWan CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                    cursor.execute(sql)
            finally:
                db.close()

            # 再次连接新创建的数据库
            db = pymysql.connect(database='XiaoWan', **connection_config)
            logger.info('数据库连接成功!')
        else:
            raise

    try:
        logger.info('初始化历史对话表...')
        with db.cursor() as cursor:
            # 建立历史对话表，若不存在，则新建
            sql = """CREATE TABLE IF NOT EXISTS history_messages(
                conversation_round INT PRIMARY KEY,
                user MEDIUMTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
                assistant MEDIUMTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            );"""
            cursor.execute(sql)
            # 保留每次启动时清空历史对话的设置
            cursor.execute("delete from history_messages;")

        db.commit()
        logger.info('历史对话表初始化成功!')
    except Exception as e:
        rollback_db(db)
        logger.error(f'初始化历史对话表时，出现错误:{e}')

    try:
        logger.info('初始化历史对话摘要表...')
        with db.cursor() as cursor:
            # 建立历史对话摘要表
            sql = """create table if not exists abstract_messages(
                abstract MEDIUMTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            );"""
            cursor.execute(sql)
            # 保留每次启动时清空摘要的设置
            cursor.execute('delete from abstract_messages;')
            # 插入一条空摘要，否则后面无法更新
            cursor.execute("INSERT INTO abstract_messages (abstract) VALUES ('');")

        db.commit()
        logger.info('摘要表初始化成功!')
    except Exception as e:
        rollback_db(db)
        logger.error(f'初始化摘要表时，出现错误{e}')

    return db


