from .tool_personas import TEXT_COMPRESS_PERSONA, ANALYSIS_PERSONA
from log_config import logger

"""
    新建人物只需创建对应文件，然后在下面import并切换对应人格即可
"""

def init_persona():
    try:
        logger.info('初始化人设...')
        # ===============当前使用的人格类===================
        # ================================================

        # ------------------------------------------------
        # 默认人物
        from .xiaowan import XiaoWan
        persona = XiaoWan
        # ================================================

        logger.info('人设初始化成功')
        return persona
    except Exception as e:
        logger.error(f'初始化人设时出现错误:{e}')

persona = init_persona()

__all__ = ["persona"]