from fastapi import APIRouter, HTTPException
from schemas.chat import ChatRequest, ChatResponse
from core.session import session, graph, get_new_state
import agent_config



router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not agent_config.analysis_agent or not agent_config.text_agent or not agent_config.friend_agent:
        raise HTTPException(400, "请先配置有效的模型")

    try:
        state = get_new_state(req.user_input)
        result = graph.invoke(state)
        session["state"] = result
        return ChatResponse(
            response=result["ai_response"],
            conversation_round=result["message"]["conversation_round"] - 1,
        )
    except Exception as e:
        raise HTTPException(500, f"AI 处理失败: {e}")



