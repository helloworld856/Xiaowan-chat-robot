"""读取并渲染聊天提示词模板。"""

from string import Template

from app.account_logging import logger
from app.config import PROMPTS_PATH


def load_prompt(name: str, **kwargs) -> str:
    path = PROMPTS_PATH / f'{name}.txt'
    try:
        template = Template(path.read_text(encoding='utf-8'))
        prompt = template.substitute(**kwargs)
    except (OSError, KeyError, ValueError) as error:
        logger.error('提示词文件 %s 加载失败（%s）', name, type(error).__name__)
        raise

    logger.info('提示词文件 %s 加载成功', name)
    return prompt
