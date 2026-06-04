import redis
from log_config import logger

"""
redis初始化，但暂时用不到redis
"""
def init_redis_db():
    try:
        logger.info('连接Redis服务器...')
        # 连接redis服务器，默认端口6379
        r = redis.Redis(
            host='localhost',
            port=6379,
            db=1,   # 选择第二个数据库
            decode_responses=True
        )

        logger.info('Redis服务器连接成功！')
        return r
    except Exception as e:
        logger.error(f'连接Redis服务器时出现错误：{e}')





