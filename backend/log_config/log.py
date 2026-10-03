import logging

from config import configer


def get_logger():
    # 获取 root logger
    logger = logging.getLogger()

    # 如果已经有 handlers，说明已经被初始化过，直接返回避免重复添加
    if logger.handlers:
        return logger

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",    # 设置输出的时间格式
        handlers=[
            logging.FileHandler(configer.log_save_path, mode='w', encoding='utf-8'),  # 启动时清空日志
        ]
    )

    return logger


logger = get_logger()
