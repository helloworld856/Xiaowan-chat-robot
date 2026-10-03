# 读取json里的人格配置
import json
from log_config import logger
from config import configer

# 初始化人格类，并返回人格
class Persona():
    def __init__(self):
        try:
            logger.info("读取人格文件，初始化人格...")
            with open(configer.root_dir_path + '/persona_config/persona.json', 'r', encoding='utf-8') as f:
                res = json.load(f)

            avatar = res.get("avatar")
            if not isinstance(avatar, str) or not avatar.strip():
                raise ValueError("avatar 必须是非空字符串")

            self.BOT_AVATAR = avatar
            self.BOT_NAME = res["name"]
            self.BOT_BIRTHDAY = res["birthday"]
            self.BOT_BIRTHPLACE = res["birthplace"]

            extra = f'你叫{res["name"]}， 出生日期：{res["birthday"]}，出生地：{res["birthplace"]}\n'
            self.FRIEND_PERSONA = extra + res['core_persona']
            logger.info(f"人格初始化成功：{res}")

        except Exception as e:
            self.BOT_AVATAR = "user_avatar.png"
            self.BOT_NAME: str = "无名氏"
            self.BOT_BIRTHDAY: str = "2004年10月13日"
            self.BOT_BIRTHPLACE: str = "中国-北京"
            self.FRIEND_PERSONA = "你是一个智能助手。"
            logger.error(f"初始化人格出错：{e},使用默认人格。")





