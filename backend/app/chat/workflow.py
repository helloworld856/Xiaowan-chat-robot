from functools import partial
from time import perf_counter

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.account_logging import logger
from app.chat.episodes import EpisodeMemoryStore
from app.chat.memory import MemoryManager
from app.chat.state import AIChatState

from .nodes import DEFAULT_COMPRESSION_TOKENS, analysis, end_node, generate, start_node


def create_graph(memorier: MemoryManager, *, compression_tokens: int = DEFAULT_COMPRESSION_TOKENS,
                 episodes: EpisodeMemoryStore | None = None,
                 recent_window_rounds: int | None = None):
    # 节点共享本账号的记忆管理器，每轮使用调度器传入的代理。
    def bind_node(name, node):
        def run(state: AIChatState, config: RunnableConfig) -> AIChatState:
            start = perf_counter()
            try:
                return node(state, memorier, config['configurable']['agents'])
            finally:
                logger.info('chat node=%s duration_ms=%.1f', name, (perf_counter() - start) * 1000)

        return run

    logger.info('初始化工作流...')
    builder = StateGraph(AIChatState)

    builder.add_node('start', bind_node('start', partial(
        start_node,
        compression_tokens=compression_tokens,
        episodes=episodes,
        recent_window_rounds=recent_window_rounds,
    )))
    builder.add_node('end', bind_node('end', end_node))
    builder.add_node('analysis', bind_node('analysis', partial(analysis, recent_window_rounds=recent_window_rounds)))
    builder.add_node('generate', bind_node('generate', partial(generate, recent_window_rounds=recent_window_rounds)))

    builder.set_entry_point('start')
    builder.add_edge('start', 'analysis')
    builder.add_edge('analysis', 'generate')
    builder.add_edge('generate', 'end')
    builder.add_edge('end', END)

    logger.info('工作流初始化完成!\n' + "=" * 60)
    return builder.compile()
