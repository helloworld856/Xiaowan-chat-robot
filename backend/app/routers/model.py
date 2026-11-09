import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from time import monotonic

from fastapi import APIRouter

import agent_config
from agent_config.agent_factory import create_model
from log_config import logger
from schemas.model import ModelConfigRequest, ModelConfigResponse
from settings_config import settings_store

router = APIRouter()
validation_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='model-test')
VALIDATION_TIMEOUT_SECONDS = 20
MODEL_SWITCH_TIMEOUT_SECONDS = 25


def validate_connection(llm):
    # 前端 30 秒超时前返回；后台验证即使稍后结束也不会继续切换模型。
    future = validation_pool.submit(llm.invoke, 'hi')
    try:
        future.result(timeout=VALIDATION_TIMEOUT_SECONDS)
    except TimeoutError:
        future.cancel()
        raise TimeoutError('模型连接测试超时')


@router.post("/model", response_model=ModelConfigResponse)
def validate_and_switch_model(request: ModelConfigRequest):
    return configure_model(request, activate=True)


@router.post('/model/test', response_model=ModelConfigResponse)
def test_model(request: ModelConfigRequest):
    return configure_model(request, activate=False)


def configure_model(request: ModelConfigRequest, activate: bool):
    """验证模型配置并切换代理"""
    deadline = monotonic() + MODEL_SWITCH_TIMEOUT_SECONDS
    merchant = request.model_merchant.lower().strip()
    model_name = request.model_name.strip()

    if not model_name:
        return ModelConfigResponse(status=False, info="模型名称不能为空")

    try:
        api_key_name = {
            "deepseek": "DEEPSEEK_API_KEY",
            "tongyi": "TONGYI_API_KEY",
        }.get(merchant)
        if api_key_name is None:
            return ModelConfigResponse(status=False, info="模型配置无效！")

        api_key = os.getenv(api_key_name)
        if not api_key:
            return ModelConfigResponse(status=False, info='尚未配置此厂商的服务端 API Key')
        llm = create_model(merchant, api_key, model_name, 0.1, max_tokens=10,
                           base_url=request.base_url, validation_timeout=8)
        validate_connection(llm)

        if not activate:
            return ModelConfigResponse(status=True, info='连接测试通过，模型配置尚未修改')

        # 三个代理和文件都准备成功后，才替换当前可用代理。
        agents = agent_config.create_agents(merchant, api_key, model_name, request.base_url)
        remaining = deadline - monotonic()
        if remaining <= 0 or not agent_config.agent_lock.acquire(timeout=remaining):
            return ModelConfigResponse(status=False, info='当前正在回复，请稍后重试切换模型')
        try:
            try:
                settings_store.update_model(request)
            except (OSError, ValueError):
                return ModelConfigResponse(status=False, info='配置保存失败，原有模型仍在使用')
            agent_config.analysis_agent, agent_config.text_agent, agent_config.friend_agent = agents
        finally:
            agent_config.agent_lock.release()

        logger.info(f"模型配置切换成功: {merchant}/{model_name}")
        return ModelConfigResponse(status=True, info=f"已切换到 {merchant}/{model_name}")

    except Exception as e:
        error_msg = str(e)
        normalized_error = error_msg.lower()
        logger.warning(f"模型配置失败: {error_msg}")

        if any(
            marker in normalized_error
            for marker in ("401", "unauthorized", "invalid_api_key", "invalid api key")
        ):
            return ModelConfigResponse(status=False, info="API Key 无效或已过期")
        elif "404" in normalized_error or "not found" in normalized_error:
            return ModelConfigResponse(status=False, info=f"模型 {model_name} 不存在")
        elif "timeout" in normalized_error or "timed out" in normalized_error:
            return ModelConfigResponse(status=False, info="请求超时，请检查网络")
        else:
            return ModelConfigResponse(status=False, info='连接失败，请检查接口地址、模型名及服务端配置')
