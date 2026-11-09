"""
代理工厂 - 动态创建不同厂商的代理
"""
from langchain.agents import create_agent
from persona_config import ANALYSIS_PERSONA, TEXT_COMPRESS_PERSONA, persona
from log_config import logger


def create_model(model_merchant, api_key, model_name, temperature, max_tokens=None, base_url='', validation_timeout=None):
    merchant = model_merchant.lower().strip()
    options = {"model": model_name, "temperature": temperature}
    if max_tokens is not None:
        options["max_tokens"] = max_tokens

    if merchant == "deepseek" or base_url:
        from langchain_openai import ChatOpenAI
        if validation_timeout is not None:
            options.update(timeout=validation_timeout, max_retries=0)
        return ChatOpenAI(
            api_key=api_key,
            base_url=base_url or "https://api.deepseek.com",
            **options,
        )
    if merchant == "tongyi":
        from langchain_community.chat_models import ChatTongyi
        if validation_timeout is not None:
            options.update(max_retries=1, model_kwargs={'request_timeout': validation_timeout})
        return ChatTongyi(dashscope_api_key=api_key, **options)
    raise ValueError(f"不支持的模型厂商: {merchant}")


def create_agents(model_merchant: str, api_key: str, model_name: str, base_url: str = ''):
    """根据厂商配置创建分析、压缩和聊天代理。"""
    try:
        logger.info(f'初始化代理: {model_merchant}/{model_name}')
        merchant = model_merchant.lower().strip()
        agent_options = (
            (ANALYSIS_PERSONA, 0.1),
            (TEXT_COMPRESS_PERSONA, 0.1),
            (persona.FRIEND_PERSONA, 1.5),
        )
        agents = tuple(
            create_agent(
                model=create_model(merchant, api_key, model_name, temperature, base_url=base_url),
                system_prompt=system_prompt,
            )
            for system_prompt, temperature in agent_options
        )

        logger.info(f'代理初始化成功: {merchant}/{model_name}')
        return agents
    except Exception as e:
        logger.error(f'初始化代理时出现错误: {e}')
        raise
