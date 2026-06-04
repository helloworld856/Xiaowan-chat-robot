from pydantic import BaseModel
from typing import Dict, Optional, List

# 请求
class HistoryRequest(BaseModel):
    num: Optional[int]    # 请求的聊天记录数
    front: bool

# 回应
class HistoryResponse(BaseModel):
    num: Optional[int]    # 返回的聊天记录数
    history_messages: List[Optional[Dict]] # 返回的聊天记录
