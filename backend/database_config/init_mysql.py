import pymysql
from config import configer
from log_config import logger
import os

def init_db():
    password = os.getenv('PASSWORD')
    # 连接数据库
    try:
        logger.info('连接数据库中...')
        db = pymysql.connect(
            host=configer.host,
            user=configer.user,
            password=password,
            port=configer.port,
            charset=configer.charset,
            database='XiaoWan'
        )

        logger.info('数据库连接成功!')
    except pymysql.MySQLError as e:
        if e.args[0] == 1049:   # unknown database
            # 创建数据库
            logger.info('数据库不存在，创建数据库...')
            db = pymysql.connect(
                host=configer.host,
                user=configer.user,
                password=password,
                port=configer.port,
                charset=configer.charset,
            )

            with db.cursor() as cursor:
                sql = "create database if not exists XiaoWan CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                cursor.execute(sql)
                db.close()

            # 再次连接新创建的数据库
            db = pymysql.connect(
                host=configer.host,
                user=configer.user,
                password=password,
                port=configer.port,
                charset=configer.charset,
                database='XiaoWan'
            )
            logger.info('数据库连接成功!')
        else:
            raise

    try:
        logger.info('初始化历史对话表...')
        # 建立数据表
        # 创建数据库的游标
        cursor = db.cursor()

        # 建立历史对话表，若不存在，则新建
        sql1 = """CREATE TABLE IF NOT EXISTS history_messages(  
            conversation_round INT PRIMARY KEY,
            user MEDIUMTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
            assistant MEDIUMTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        );"""

        cursor.execute(sql1)
        # 清空历史对话表
        sql2 = "delete from history_messages;"
        cursor.execute(sql2)

        db.commit()
        logger.info('历史对话表初始化成功!')
    except Exception as e:
        logger.error(f'初始化历史对话表时，出现错误:{e}')

    try:
        logger.info('初始化历史对话摘要表...')
        cursor = db.cursor()

        # 建立历史对话摘要表
        sql1 = """create table if not exists abstract_messages(
            abstract MEDIUMTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        );"""
        cursor.execute(sql1)

        # 清空历史对话摘要表
        sql2 = 'delete from abstract_messages;'
        cursor.execute(sql2)

        # 插入点数据，否则后面无法更新
        cursor.execute("INSERT INTO abstract_messages (abstract) VALUES ('');")

        db.commit()
        logger.info('摘要表初始化成功!')
    except Exception as e:
        logger.error(f'初始化摘要表时，出现错误{e}')


    return db


