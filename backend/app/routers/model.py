import os

from fastapi import APIRouter

from agent_config import switch_agents
from agent_config.agent_factory import create_model
from log_config import logger
from schemas.model import ModelConfigRequest, ModelConfigResponse

router = APIRouter()


@router.post("/model", response_model=ModelConfigResponse)
async def validate_and_switch_model(request: ModelConfigRequest):
    """验证模型配置并切换代理"""
    merchant = request.model_merchant.lower().strip()
    model_name = request.model_name.lower().strip()

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
        llm = create_model(merchant, api_key, model_name, 0.1, max_tokens=10)
        llm.invoke("hi")

        switch_agents(merchant, api_key, model_name)

        logger.info(f"模型配置切换成功: {merchant}/{model_name}")
        return ModelConfigResponse(status=True, info=f"已切换到 {merchant}/{model_name}")

    except Exception as e:
        error_msg = str(e)
        logger.warning(f"模型配置失败: {error_msg}")

        if "401" in error_msg or "Unauthorized" in error_msg or "Invalid" in error_msg.lower():
            return ModelConfigResponse(status=False, info="API Key 无效或已过期")
        elif "404" in error_msg or "not found" in error_msg.lower():
            return ModelConfigResponse(status=False, info=f"模型 {model_name} 不存在")
        elif "timeout" in error_msg.lower():
            return ModelConfigResponse(status=False, info="请求超时，请检查网络")
        else:
            return ModelConfigResponse(status=False, info=f"验证失败: {error_msg}")
