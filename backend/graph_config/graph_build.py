from time import perf_counter

from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from log_config import logger
from memory_config import MemoryManager
from state_config import AIChatState

from .nodes import start_node, end_node, analysis, generate


def create_graph(memorier: MemoryManager):
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

    builder.add_node('start', bind_node('start', start_node))
    builder.add_node('end', bind_node('end', end_node))
    builder.add_node('analysis', bind_node('analysis', analysis))
    builder.add_node('generate', bind_node('generate', generate))

    builder.set_entry_point('start')
    builder.add_edge('start', 'analysis')
    builder.add_edge('analysis', 'generate')
    builder.add_edge('generate', 'end')
    builder.add_edge('end', END)

    logger.info('工作流初始化完成!\n' + "="*60)
    return builder.compile()


