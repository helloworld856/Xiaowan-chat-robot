# 读取json里的人格配置
import json
from pathlib import Path

from app.account_logging import logger
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


# ═══════════════════════════════════════════════════════════════
#                  分析与压缩助手的人设（固定文件）
# ═══════════════════════════════════════════════════════════════

# 分析机器人人设
ANALYSIS_PERSONA = """
你是一个专业的对话分析专家。
你的任务是准确分析用户输入的意图、情感和最适合的回复类型。

【重要强制要求】
1. 必须仅返回标准的 JSON 字符串。
2. 严禁使用 ```json ... ``` 代码块包裹。
3. 严禁输出任何分析过程、问候语或额外文本。
"""

# 文本压缩机器人人设
TEXT_COMPRESS_PERSONA = """
你是一个专业的对话摘要专家。
你的任务是将历史对话压缩成简洁的摘要，保留关键信息：
- 用户提到的重要事件、人物、地点
- 用户表达的情感和心情变化
- 用户的偏好和习惯
- 重要的约定或承诺
请用简洁的语言概括，方便后续对话参考。
"""


persona = Persona()
