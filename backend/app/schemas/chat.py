from pydantic import BaseModel
from typing import List, Optional

class ChatRequest(BaseModel):
    user_input: str

class ChatResponse(BaseModel):
    response: List
    conversation_round: Optional[int]
