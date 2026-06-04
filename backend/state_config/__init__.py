from .state import AIChatState

State: AIChatState = {
    "user_input": '',
    "analysis_result": {},
    "ai_response": [],
    'ai_monologue': '',
    'ai_emotion': '',
    'ai_action': '',
    "total_tokens": 0,
    'message': {
        'conversation_round': 0,
        'user': '',
        'assistant': [],
    },
    'start_time': 0,

}