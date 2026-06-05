"""
代理配置模块 - 提供全局代理实例和动态切换功能
"""
import os
from dotenv import load_dotenv
from .agent_factory import create_agents
from log_config import logger

load_dotenv()

# 默认配置（从环境变量读取）
DEFAULT_MERCHANT = os.getenv("DEFAULT_MODEL_MERCHANT", "deepseek")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL_NAME", "deepseek-v4-flash")

# 根据默认厂商获取 API Key
if DEFAULT_MERCHANT == "deepseek":
    DEFAULT_API_KEY = os.getenv("DEEPSEEK_API_KEY")
elif DEFAULT_MERCHANT == "tongyi":
    DEFAULT_API_KEY = os.getenv("TONGYI_API_KEY")
else:
    DEFAULT_API_KEY = None

# 全局代理实例（启动时初始化）
analysis_agent = None
text_agent = None
friend_agent = None

# 当前配置
current_config = {
    "merchant": DEFAULT_MERCHANT,
    "model": DEFAULT_MODEL,
    "api_key": DEFAULT_API_KEY,
}


# def init_default_agents():
#     """使用默认配置初始化代理"""
#     global analysis_agent, text_agent, friend_agent
#     try:
#         logger.info(f'使用默认配置初始化代理: {DEFAULT_MERCHANT}/{DEFAULT_MODEL}')
#         agents = create_agents(DEFAULT_MERCHANT, DEFAULT_API_KEY, DEFAULT_MODEL)
#         analysis_agent, text_agent, friend_agent = agents
#         logger.info('默认代理初始化完成')
#     except Exception as e:
#         logger.error(f'默认代理初始化失败: {e}')
#         raise


def switch_agents(merchant: str, api_key: str, model_name: str):
    """
    切换到新的代理配置
    
    Args:
        merchant: 模型厂商
        api_key: API密钥
        model_name: 模型名称
    """
    global analysis_agent, text_agent, friend_agent, current_config
    
    try:
        logger.info(f'切换代理配置: {merchant}/{model_name}')
        agents = create_agents(merchant, api_key, model_name)
        
        # 切换成功，更新全局代理
        analysis_agent, text_agent, friend_agent = agents
        
        # 更新当前配置
        current_config["merchant"] = merchant
        current_config["model"] = model_name
        current_config["api_key"] = api_key
        
        logger.info(f'代理切换成功: {merchant}/{model_name}')
        return True
    except Exception as e:
        logger.error(f'代理切换失败: {e}')
        raise


# # 启动时自动初始化默认代理
# init_default_agents()


__all__ = [
    'analysis_agent',
    'text_agent', 
    'friend_agent',
    'switch_agents',
    'current_config',
]
