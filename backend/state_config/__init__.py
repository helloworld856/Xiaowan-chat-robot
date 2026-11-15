from .state import AIChatState


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
