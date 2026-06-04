from typing import List

"""
    人物基类，所有新建的人物类都必须继承此类并覆写里面的默认值
"""

class Base_persona():
    # ──────────────────── 基本信息 ────────────────────
    # 头像
    BOT_AVATAR = None
    # 人物名
    BOT_NAME: str = "无名氏"
    # 出生年月日
    BOT_BIRTHDAY: str = "2004年10月13日"
    # 出生地
    BOT_BIRTHPLACE: str = "中国-北京"
    # 核心人设
    FRIEND_PERSONA = "你是一个智能助手。"


