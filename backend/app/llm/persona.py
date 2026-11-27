# 读取json里的人格配置
import json

from app import config
from app.account_logging import logger

# 初始化人格类，并返回人格
class Persona:
    def __init__(self):
        try:
            path = config.BACKEND_ROOT / 'persona_config' / 'persona.json'
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

        # 配置缺失或格式错误时使用默认人设，让聊天入口仍可启动。
        except (OSError, ValueError):
            self.BOT_AVATAR = "xiaowan.png"
            self.BOT_NAME: str = "无名氏"
            self.BOT_BIRTHDAY: str = "2004年10月13日"
            self.BOT_BIRTHPLACE: str = "中国-北京"
            self.FRIEND_PERSONA = "你是一个智能助手。"
            logger.warning('无法读取有效的人格配置，使用默认人格')


# 辅助代理的提示词
TEXT_COMPRESS_PERSONA = """
你替小晚更新旧对话的记忆。把已有摘要与本批对话合并，交回一份可独立读懂的新摘要。
优先保留以后可能接上的信息，例如用户明确表达的偏好、重要的人和事、约定、进展和尚未解决的问题。保留必要的称呼、时间和归属。
记清是谁说的、事情到了哪一步。提议、用户接受的安排和已经发生的事分别记录。用户有明确更正时，用最新说法替换旧信息。遇到「明天」这类相对时间，只有日期可确定时才换成具体日期，否则注明是当时的说法。
保留用户明确说过的感受。玩笑、假设和猜测有必要保留时，注明原来的语境。
删去寒暄、重复和通用建议，只输出简明的中文摘要正文。没有新细节值得保留时，沿用仍有用的旧摘要。
"""


persona = Persona()
