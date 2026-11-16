"""
代理工厂 - 动态创建不同厂商的代理
"""
from langchain.agents import create_agent
from persona_config import ANALYSIS_PERSONA, TEXT_COMPRESS_PERSONA, persona
from log_config import logger

CHAT_MODEL_TIMEOUT_SECONDS = 45
DASHSCOPE_OFFICIAL_BASE_URL = 'https://dashscope.aliyuncs.com/api/v1'


def create_model(model_merchant, api_key, model_name, temperature, max_tokens=None, base_url='',
                 validation_timeout=None, *, official_only=False):
    merchant = model_merchant.lower().strip()
    options = {"temperature": temperature}
    if max_tokens is not None:
        options["max_tokens"] = max_tokens

    if official_only and base_url:
        raise ValueError('个人模型只支持厂商官方地址')
    if merchant == "deepseek" or base_url:
        from langchain_openai import ChatOpenAI
        if validation_timeout is not None:
            options.update(timeout=validation_timeout, max_retries=0)
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url or "https://api.deepseek.com",
            # 基础压缩由标准库支持，避免运行环境中可选 Brotli 解码器不兼容。
            default_headers={'Accept-Encoding': 'gzip, deflate'},
            **options,
        )
    if merchant == "tongyi":
        from langchain_community.chat_models import ChatTongyi
        client_options = {}
        if official_only:
            # Do not inherit the provider SDK's process-wide endpoint override for personal keys.
            options['base_address'] = DASHSCOPE_OFFICIAL_BASE_URL
        if validation_timeout is not None:
            options['request_timeout'] = validation_timeout
            client_options['max_retries'] = 1
        return ChatTongyi(model=model_name, dashscope_api_key=api_key, model_kwargs=options,
                          **client_options)
    raise ValueError(f"不支持的模型厂商: {merchant}")


def create_agents(model_merchant: str, api_key: str, model_name: str, base_url: str = '', *, official_only=False):
    """根据厂商配置创建分析、压缩和聊天代理。"""
    try:
        logger.info('初始化模型代理')
        merchant = model_merchant.lower().strip()
        agent_options = (
            (ANALYSIS_PERSONA, 0.1),
            (TEXT_COMPRESS_PERSONA, 0.1),
            (persona.FRIEND_PERSONA, 1.5),
        )
        agents = tuple(
            create_agent(
                model=create_model(merchant, api_key, model_name, temperature, base_url=base_url,
                                   validation_timeout=CHAT_MODEL_TIMEOUT_SECONDS, official_only=official_only),
                system_prompt=system_prompt,
            )
            for system_prompt, temperature in agent_options
        )

        logger.info('模型代理初始化成功')
        return agents
    except Exception as e:
        logger.error(f'初始化代理时出现错误: {type(e).__name__}')
        raise
