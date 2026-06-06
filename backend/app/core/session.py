"""
全局会话管理
"""
from copy import deepcopy
from graph_config import create_graph
from state_config import State

graph = create_graph()

session = {
    "session_id": 0,
    "state": State,
}
