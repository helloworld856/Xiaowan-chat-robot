"""
代理配置模块 - 提供全局代理实例和动态切换功能
"""
from .agent_factory import create_agents
from threading import RLock

analysis_agent = None
text_agent = None
friend_agent = None
active_model = None
agent_lock = RLock()
