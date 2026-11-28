from typing import Dict, List, Optional, TypedDict, Union


# 定义数据状态
class AIChatState(TypedDict, total=False):
    # 用户输入
    user_input: Optional[str]
    # 用户画像摘要（手动资料 + 动态印象）
    user_portrait: str
    # 按需混合召回的情景事件记忆
    recalled_episodes: Optional[str]
    # 本轮用户消息附带的公开附件元数据（随消息落库）
    attachments: List[Dict]
    # 本轮送入视觉模型的图片 Data URL 列表（含本轮新图或引用的旧图）
    turn_images: List[str]
    # 本轮提取的轻量文档正文列表
    turn_documents: List[Dict]
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


# 每次创建独立的列表和字典，避免不同账号共享状态。
def create_initial_state(next_round: int = 0) -> AIChatState:
    return {
        'user_input': '',
        'user_portrait': '暂无',
        'recalled_episodes': None,
        'attachments': [],
        'turn_images': [],
        'turn_documents': [],
        'ai_response': [],
        'saved': False,
        'ai_monologue': '',
        'ai_emotion': '',
        'ai_action': '',
        'total_tokens': 0,
        'message': {'conversation_round': next_round, 'user': '', 'assistant': []},
        'start_time': 0,
    }
