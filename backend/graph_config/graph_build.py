from .nodes import start_node, end_node, analysis, generate
from langgraph.graph import StateGraph, END
from log_config import logger
from state_config import AIChatState


def create_graph():
    try:
        logger.info('初始化工作流...')
        builder = StateGraph(AIChatState)

        builder.add_node('start', lambda state: start_node(state))
        builder.add_node('end', lambda state: end_node(state))
        builder.add_node('analysis', lambda state: analysis(state))
        builder.add_node('generate', lambda state: generate(state))

        builder.set_entry_point('start')
        builder.add_edge('start', 'analysis')
        builder.add_edge('analysis', 'generate')
        builder.add_edge('generate', 'end')
        builder.add_edge('end', END)

        logger.info('工作流初始化完成!\n' + "="*60)
        return builder.compile()
    except Exception as e:
        logger.error(f'工作流初始化时出现错误:{e}')


