"""
代理工厂 - 动态创建不同厂商的代理
"""
from langchain.agents import create_agent
from persona_config import ANALYSIS_PERSONA, TEXT_COMPRESS_PERSONA, persona
from log_config import logger


def create_agents(model_merchant: str, api_key: str, model_name: str):
    """
    根据配置创建所有代理
    
    Args:
        model_merchant: 模型厂商 (deepseek / tongyi)
        api_key: API密钥
        model_name: 模型名称
    
    Returns:
        tuple: (analysis_agent, text_agent, friend_agent)
    """
    try:
        logger.info(f'初始化代理: {model_merchant}/{model_name}')
        
        merchant = model_merchant.lower().strip()
        
        # ============ DeepSeek ============
        if merchant == "deepseek":
            from langchain_openai import ChatOpenAI
            
            # 分析代理（低温度）
            llm_analysis = ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url="https://api.deepseek.com",
                temperature=0.1,
            )
            analysis_agent = create_agent(
                model=llm_analysis,
                system_prompt=ANALYSIS_PERSONA,
            )
            
            # 文本压缩代理（低温度）
            llm_compress = ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url="https://api.deepseek.com",
                temperature=0.1,
            )
            text_agent = create_agent(
                model=llm_compress,
                system_prompt=TEXT_COMPRESS_PERSONA,
            )

            # 聊天代理（高温度）
            llm_friend = ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url="https://api.deepseek.com",
                temperature=1.5,
            )
            friend_agent = create_agent(
                model=llm_friend,
                system_prompt=persona.FRIEND_PERSONA,
            )
        
        # ============ 通义千问 ============
        elif merchant == "tongyi":
            from langchain_community.chat_models import ChatTongyi
            
            # 分析代理
            llm_analysis = ChatTongyi(
                model=model_name,
                dashscope_api_key=api_key,
                temperature=0.1,
            )
            analysis_agent = create_agent(
                model=llm_analysis,
                system_prompt=ANALYSIS_PERSONA,
            )
            
            # 文本压缩代理
            llm_compress = ChatTongyi(
                model=model_name,
                dashscope_api_key=api_key,
                temperature=0.1,
            )
            text_agent = create_agent(
                model=llm_compress,
                system_prompt=TEXT_COMPRESS_PERSONA,
            )
            
            # 聊天代理
            llm_friend = ChatTongyi(
                model=model_name,
                dashscope_api_key=api_key,
                temperature=1.5,
            )
            friend_agent = create_agent(
                model=llm_friend,
                system_prompt=persona.FRIEND_PERSONA,
            )
        
        else:
            raise ValueError(f"不支持的模型厂商: {merchant}")
        
        logger.info(f'代理初始化成功: {merchant}/{model_name}')
        return analysis_agent, text_agent, friend_agent
    
    except Exception as e:
        logger.error(f'初始化代理时出现错误: {e}')
        raise
