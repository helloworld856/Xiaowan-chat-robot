"""通用的prompt文件加载器"""
from string import Template
from app.config import PROMPTS_PATH
from app.account_logging import logger


def load_prompt(name: str, **kwargs) -> str:
    logger.info(f'加载提示词文件{name}...')
    try:
        path = PROMPTS_PATH / f"{name}.txt"

        template = Template(
            path.read_text(encoding="utf-8")
        )

        logger.info(f"提示词文件{name}加载成功!!!")
        return template.substitute(**kwargs)

    except Exception as e:
        logger.error(f'提示词文件{name}加载失败：{e}')
