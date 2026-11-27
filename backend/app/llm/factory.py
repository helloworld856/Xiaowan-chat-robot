"""
代理工厂 - 动态创建不同厂商的代理
"""
from os import getenv

import httpx
from langchain.agents import create_agent
from app.llm.model_catalog import ModelAdapter
from app.llm.persona import TEXT_COMPRESS_PERSONA, persona
from app.llm.public_endpoint import public_http_clients
from app.account_logging import logger
from tools_config.registry import CHAT_TOOLS

CHAT_MODEL_TIMEOUT_SECONDS = 45


def create_model(model_merchant, api_key, model_name, temperature, max_tokens=None, *, base_url: str,
                 adapter: ModelAdapter, validation_timeout=None, public_only: bool = False,
                 json_output: bool = False):
    if public_only and adapter != 'openai':
        raise ValueError('公网自定义地址须使用 OpenAI 兼容接口')
    options = {"temperature": temperature}
    if max_tokens is not None:
        options["max_tokens"] = max_tokens

    if adapter == 'openai':
        from langchain_openai import ChatOpenAI
        # 仅向已支持 JSON 模式的 DeepSeek 官方接口传该参数。
        if json_output and model_merchant == 'deepseek' and base_url.rstrip('/') in (
            'https://api.deepseek.com', 'https://api.deepseek.com/v1',
        ):
            # 由 SDK 合并进 HTTP 请求体，沿用现有工具调用与本地回复解析。
            options['extra_body'] = {'response_format': {'type': 'json_object'}}
        if validation_timeout is not None:
            options.update(timeout=validation_timeout, max_retries=0)
        timeout = validation_timeout or CHAT_MODEL_TIMEOUT_SECONDS
        if public_only:
            options.update(public_http_clients(base_url, timeout))
        else:
            # 模型请求携带密钥，禁止 OpenAI 客户端自动跟随重定向。
            http_options = {'timeout': timeout, 'follow_redirects': False, 'proxy': getenv('OPENAI_PROXY') or None}
            options.update(http_client=httpx.Client(**http_options),
                           http_async_client=httpx.AsyncClient(**http_options), openai_proxy='')
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            # 基础压缩由标准库支持，避免运行环境中可选 Brotli 解码器不兼容。
            default_headers={'Accept-Encoding': 'gzip, deflate'},
            **options,
        )
    if adapter == 'dashscope':
        from langchain_community.chat_models import ChatTongyi
        client_options = {}
        options['base_address'] = base_url
        if validation_timeout is not None:
            options['request_timeout'] = validation_timeout
            client_options['max_retries'] = 1
        return ChatTongyi(model=model_name, dashscope_api_key=api_key, model_kwargs=options,
                          **client_options)
    raise ValueError('不支持的模型接口协议')


def create_agents(model_merchant: str, api_key: str, model_name: str, *, base_url: str, adapter: ModelAdapter,
                  public_only: bool = False):
    """根据厂商配置创建摘要和聊天代理。"""
    try:
        logger.info('初始化模型代理')
        # 摘要使用较低温度，角色回复留更多表达空间。
        agent_options = (
            (TEXT_COMPRESS_PERSONA, 0.1, [], False),
            (persona.FRIEND_PERSONA, 0.8, CHAT_TOOLS, True),
        )
        agents = tuple(
            create_agent(
                model=create_model(
                    model_merchant,
                    api_key,
                    model_name,
                    temperature,
                    base_url=base_url,
                    validation_timeout=CHAT_MODEL_TIMEOUT_SECONDS, adapter=adapter,
                    public_only=public_only, json_output=json_output,
                ),
                tools=tools,
                system_prompt=system_prompt,
            )
            for system_prompt, temperature, tools, json_output in agent_options
        )

        logger.info('模型代理初始化成功')
        return agents
    except Exception as e:
        logger.error(f'初始化代理时出现错误: {type(e).__name__}')
        raise
