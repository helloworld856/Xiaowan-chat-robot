from typing import TypedDict, Dict, List, Optional


# 定义数据状态
class AIChatState(TypedDict):
    # 用户输入
    user_input: Optional[str]
    # 分析结果
    analysis_result: Dict
    # 回复
    ai_response: List
    # 内心独白
    ai_monologue: Optional[str]
    # 情绪
    ai_emotion: Optional[str]
    # 动作
    ai_action: Optional[str]
    # 总tokens用量
    total_tokens: int

    # 记录每轮对话
    message: Dict
    # 记录开始执行时间
    start_time: float