"""
全局会话管理
"""
from graph_config import create_graph
from state_config import State

graph = create_graph()

session = {
    "state": State,
}
