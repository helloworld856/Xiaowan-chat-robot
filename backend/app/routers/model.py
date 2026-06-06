from schemas.model import ModelConfigRequest, ModelConfigResponse
from log_config import logger
from fastapi import APIRouter
import os

router = APIRouter()

@router.post("/model", response_model=ModelConfigResponse)
async def validate_and_switch_model(request: ModelConfigRequest):
    """验证模型配置并切换代理"""
    merchant = request.model_merchant.lower().strip()
    # api_key = request.api_key.strip()
    model_name = request.model_name.lower().strip()

    # 参数检查
    # if not api_key:
    #     return ModelConfigResponse(status=False, info=f"API Key为{api_key}")
    if not model_name:
        return ModelConfigResponse(status=False, info="模型名称不能为空")

    try:
        # 根据厂商创建 LLM 实例进行测试
        if merchant == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY")
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url="https://api.deepseek.com",
                temperature=0.1,
                max_tokens=10,  # 测试用，省token
            )
        elif merchant == "tongyi":
            api_key = os.getenv("TONGYI_API_KEY")
            from langchain_community.chat_models import ChatTongyi
            llm = ChatTongyi(
                model=model_name,
                dashscope_api_key=api_key,
                temperature=0.1,
                max_tokens=10,
            )
        else:
            return ModelConfigResponse(status=False, info=f"模型配置无效！")

        # 发送测试请求验证配置
        llm.invoke("hi")

        # 验证成功，切换代理
        from agent_config import switch_agents
        switch_agents(merchant, api_key, model_name)

        logger.info(f"模型配置切换成功: {merchant}/{model_name}")
        return ModelConfigResponse(status=True, info=f"已切换到 {merchant}/{model_name}")

    except Exception as e:
        error_msg = str(e)
        logger.warning(f"模型配置失败: {error_msg}")

        # 友好的错误提示
        if "401" in error_msg or "Unauthorized" in error_msg or "Invalid" in error_msg.lower():
            return ModelConfigResponse(status=False, info="API Key 无效或已过期")
        elif "404" in error_msg or "not found" in error_msg.lower():
            return ModelConfigResponse(status=False, info=f"模型 {model_name} 不存在")
        elif "timeout" in error_msg.lower():
            return ModelConfigResponse(status=False, info="请求超时，请检查网络")
        else:
            return ModelConfigResponse(status=False, info=f"验证失败: {error_msg}")
