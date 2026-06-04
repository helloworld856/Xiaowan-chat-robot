"""
全局会话管理
"""
from copy import deepcopy
from graph_config import create_graph
from state_config import State
from memory_config import memorier

graph = create_graph()

session = {
    "session_id": 0,
    "state": State,
}

def get_new_state(user_input: str):
    current_state = deepcopy(session["state"])
    current_state["user_input"] = user_input
    return current_state
