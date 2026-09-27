from .nodes import start_node, end_node, analysis, generate
from langgraph.graph import StateGraph, END
from log_config import logger
from state_config import AIChatState


def create_graph():
    logger.info('初始化工作流...')
    builder = StateGraph(AIChatState)

    builder.add_node('start', start_node)
    builder.add_node('end', end_node)
    builder.add_node('analysis', analysis)
    builder.add_node('generate', generate)

    builder.set_entry_point('start')
    builder.add_edge('start', 'analysis')
    builder.add_edge('analysis', 'generate')
    builder.add_edge('generate', 'end')
    builder.add_edge('end', END)

    logger.info('工作流初始化完成!\n' + "="*60)
    return builder.compile()


