import re
from time import time

from app.account_logging import logger
from app.chat.memory import MemoryManager
from app.chat.state import AIChatState
from app.llm.token_counter import count_tokens
from app.llm.output_parser import model_text, parse_model_json, safe_parse_json
from app.system_config import ChatConfig
from app.prompt_loader import load_prompt


DEFAULT_COMPRESSION_TOKENS = ChatConfig().history_compression_tokens
DEFAULT_ANALYSIS = {'intent': '一般对话', 'emotion': '无情感波动', 'answer': '日常互动'}
DEFAULT_REPLY = '嗯...我刚刚有点没组织好语言，你再说一遍嘛'


def get_last_message(response: dict):
    messages = response.get("messages", [])
    if not messages:
        raise ValueError("模型返回结果中没有 messages")
    return messages[-1]


def get_total_tokens(message) -> int:
    metadata = getattr(message, 'response_metadata', None)
    # 兼容旧适配器的 Token 字段，并优先采用统一的用量元数据。
    legacy_usage = metadata.get('token_usage') if isinstance(metadata, dict) else None
    for usage in (getattr(message, 'usage_metadata', None), legacy_usage):
        if isinstance(usage, dict):
            tokens = usage.get('total_tokens')
            if type(tokens) is int and tokens >= 0:
                return tokens
    return 0


# 基础结点定义
# 定义开始结点
def start_node(state: AIChatState, memorier: MemoryManager, agents, *,
               compression_tokens: int = DEFAULT_COMPRESSION_TOKENS) -> AIChatState:
    try:
        logger.info('执行结点"start_node"...')
        state['start_time'] = time()

        # 清空上轮的回复
        state['ai_response'] = []
        state['saved'] = False
        state['total_tokens'] = 0
        memorier.refresh_if_needed()
        state['message']['conversation_round'] = memorier.next_round

        # 计算历史文本的 Token 用量。
        res = []
        o = 0
        for msg in memorier.history_cache:
            o += 1
            res.append(msg['user'])
            res.extend(msg['assistant'])
        history_text = memorier.abstract_cache + ','.join(res)
        t = count_tokens(history_text) if history_text else 0
        t += 4 * o  # 估算每轮历史记录的字段开销。

        if memorier.has_older_history or t > compression_tokens:
            # 生成历史对话摘要
            _, summary_agent, _ = agents
            memorier.compress_history(summary_agent)

        logger.info('结点"start_node"执行完成！')
        return state
    except Exception as error:
        logger.error('start node failed: %s', type(error).__name__)
        raise


# 定义结束结点
def end_node(state: AIChatState, memorier: MemoryManager, _agents) -> AIChatState:
    logger.info('执行结点"end_node"...')

    # 保存失败时仍返回回复，由接口明确告知前端。
    state['saved'] = memorier.record_message(state['message'])

    logger.info('本轮对话 %d：回复 %d 条，分析与回复 Token %s，耗时 %.2f 秒，保存%s',
                state['message']['conversation_round'] + 1, len(state['ai_response']),
                state['total_tokens'] or '未返回', time() - state['start_time'],
                '成功' if state['saved'] else '失败')

    return state


# AI结点定义
# 分析用户输入
def analysis(state: AIChatState, memorier: MemoryManager, agents) -> AIChatState:
    try:
        logger.info('执行结点"analysis"...')
        user_input = state.get('user_input')

        # 构建分析提示
        analysis_prompt = load_prompt(
            "analysis_prompt",
            history=memorier.history_cache,
            abstract=memorier.abstract_cache or '无',
            user_input=user_input
        )

        analysis_agent, _, _ = agents
        response = analysis_agent.invoke(
            {
                "messages": [{"role": "user", "content": analysis_prompt}]
            }
        )
        last_message = get_last_message(response)

        # 提取出回复并转成字典格式（使用容错解析）
        defaults = DEFAULT_ANALYSIS
        ai_response = safe_parse_json(last_message.content, defaults, expected_keys=defaults.keys(), stage='消息分析')

        state['analysis_result'] = {}
        for field, default in defaults.items():
            value = ai_response.get(field)
            state['analysis_result'][field] = value if isinstance(value, str) else default
        state['total_tokens'] += get_total_tokens(last_message)

        logger.info('结点"analysis"执行完成！')
        return state
    except Exception as error:
        logger.error('analysis node failed: %s', type(error).__name__)
        raise


# 生成回复结点
def generate(state: AIChatState, memorier: MemoryManager, agents) -> AIChatState:
    try:
        logger.info('执行结点"generate"...')
        user_input = state.get('user_input', '')

        # 构建回府提示词
        answer_prompt = load_prompt(
            "answer_prompt",
            history=memorier.history_cache,
            abstract=memorier.abstract_cache,
            user_input=user_input,
            intent=state['analysis_result'].get('intent', '一般对话'),
            emotion=state['analysis_result'].get('emotion', '无情绪波动'),
            answer=state['analysis_result'].get('answer', '日常互动')
        )

        _, _, reply_agent = agents
        response = reply_agent.invoke(
            {"messages": [{"role": "user", "content": answer_prompt}]},
            config={"recursion_limit": 6}
        )
        last_message = get_last_message(response)
        text = model_text(last_message.content)
        ai_response = parse_model_json(text, ('reply',))
        # 纯文本可直接显示，疑似损坏的结构化输出用默认回复兜底。
        if ai_response is None:
            structured_field = re.search(r'''[{,]\s*["'“]?(?:reply|emotion|action|inner monologue)["'”]?\s*[:：]''', text)
            if text and not text.startswith(('{', '[', '```')) and not structured_field:
                ai_response = {'reply': [text]}
            else:
                logger.warning('回复生成：模型输出无法解析，使用默认回复')
                ai_response = {'reply': [DEFAULT_REPLY], 'inner monologue': [], 'emotion': '困惑', 'action': ''}

        # 记录回复，情绪、动作
        reply = ai_response.get('reply', [])
        if isinstance(reply, str):
            reply = [reply]
        elif not isinstance(reply, list):
            reply = []
        messages = [part for part in reply if isinstance(part, str) and part.strip()]
        if not messages:
            logger.warning('回复生成：模型输出缺少有效回复，使用默认回复')
        state['ai_response'] = messages or [DEFAULT_REPLY]

        monologue = ai_response.get('inner monologue', '')
        if not isinstance(monologue, str) and (
            not isinstance(monologue, list) or any(not isinstance(part, str) for part in monologue)
        ):
            monologue = []
        emotion = ai_response.get('emotion')
        action = ai_response.get('action')
        state['ai_monologue'] = monologue
        state['ai_emotion'] = emotion if isinstance(emotion, str) else ''
        state['ai_action'] = action if isinstance(action, str) else ''

        state['total_tokens'] += get_total_tokens(last_message)

        # 记录对话
        state['message']['user'] = user_input
        state['message']['assistant'] = state['ai_response']

        logger.info('结点"generate"执行完成！')
        return state
    except Exception as error:
        logger.error('generate node failed: %s', type(error).__name__)
        raise
