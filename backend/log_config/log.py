import logging
from config import configer


def get_logger():
    # 获取 root logger
    logger = logging.getLogger()
    
    # 如果已经有 handlers，说明已经被初始化过，直接返回避免重复添加
    if logger.handlers:
        return logger

    # 启动时清理日志文件（如果需要每次启动都清理）
    with open(configer.log_save_path, 'w', encoding='utf-8') as f:
        f.write('') # 清空内容
        
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",    # 设置输出的时间格式
        handlers=[
            logging.FileHandler(configer.log_save_path, mode='a', encoding='utf-8'),  # mode='a' 追加模式
            # logging.StreamHandler()  # 创建控制台处理器，将日志输出到控制台
        ]
    )

    return logger

logger = get_logger()