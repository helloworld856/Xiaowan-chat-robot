from typing import Dict, List, Optional, TypedDict, Union


# 定义数据状态
class AIChatState(TypedDict):
    # 用户输入
    user_input: Optional[str]
    # 分析结果
    analysis_result: Dict
    # 回复
    ai_response: List[str]
    # 本轮对话是否已保存到数据库
    saved: bool
    # 内心独白
    ai_monologue: Optional[Union[str, List[str]]]
    # 情绪
    ai_emotion: Optional[str]
    # 动作
    ai_action: Optional[str]
    # 本轮分析与回复的 Token 用量
    total_tokens: int

    # 记录每轮对话
    message: Dict
    # 记录开始执行时间
    start_time: float


def create_initial_state(next_round: int = 0) -> AIChatState:
    return {
        'user_input': '',
        'analysis_result': {},
        'ai_response': [],
        'saved': False,
        'ai_monologue': '',
        'ai_emotion': '',
        'ai_action': '',
        'total_tokens': 0,
        'message': {'conversation_round': next_round, 'user': '', 'assistant': []},
        'start_time': 0,
    }
