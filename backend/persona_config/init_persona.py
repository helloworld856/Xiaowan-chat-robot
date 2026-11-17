# 读取json里的人格配置
import json
from pathlib import Path

from log_config import logger
from app.config import configer

# 初始化人格类，并返回人格
class Persona:
    def __init__(self):
        try:
            path = Path(configer.root_dir_path) / 'persona_config' / 'persona.json'
            data = json.loads(path.read_text(encoding='utf-8'))
            required = ('name', 'avatar', 'birthday', 'birthplace', 'core_persona')
            if (not isinstance(data, dict) or any(not isinstance(data.get(field), str) for field in required)
                    or not data['avatar'].strip()):
                raise ValueError('人格配置字段无效')

            self.BOT_AVATAR = data['avatar'].strip()
            self.BOT_NAME = data['name']
            self.BOT_BIRTHDAY = data['birthday']
            self.BOT_BIRTHPLACE = data['birthplace']
            extra = f'你叫{self.BOT_NAME}， 出生日期：{self.BOT_BIRTHDAY}，出生地：{self.BOT_BIRTHPLACE}\n'
            self.FRIEND_PERSONA = extra + data['core_persona']
            logger.info('人格初始化成功')

        except (OSError, ValueError):
            self.BOT_AVATAR = "xiaowan.png"
            self.BOT_NAME: str = "无名氏"
            self.BOT_BIRTHDAY: str = "2004年10月13日"
            self.BOT_BIRTHPLACE: str = "中国-北京"
            self.FRIEND_PERSONA = "你是一个智能助手。"
            logger.warning('无法读取有效的人格配置，使用默认人格')
