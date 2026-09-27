"""
代理配置模块 - 提供全局代理实例和动态切换功能
"""
from .agent_factory import create_agents
from log_config import logger

analysis_agent = None
text_agent = None
friend_agent = None

def switch_agents(merchant: str, api_key: str, model_name: str):
    """
    切换到新的代理配置
    
    Args:
        merchant: 模型厂商
        api_key: API密钥
        model_name: 模型名称
    """
    global analysis_agent, text_agent, friend_agent
    
    try:
        logger.info(f'切换代理配置: {merchant}/{model_name}')
        agents = create_agents(merchant, api_key, model_name)
        
        # 切换成功，更新全局代理
        analysis_agent, text_agent, friend_agent = agents
        
        logger.info(f'代理切换成功: {merchant}/{model_name}')
        return True
    except Exception as e:
        logger.error(f'代理切换失败: {e}')
        raise

__all__ = [
    'analysis_agent',
    'text_agent', 
    'friend_agent',
    'switch_agents',
]
