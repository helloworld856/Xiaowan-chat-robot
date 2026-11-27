import re
from time import time

from app.account_logging import logger
from app.chat.memory import MemoryManager, format_history_dialogue
from app.chat.progress import ChatCancelled, check_cancelled
from app.chat.state import AIChatState
from app.llm.persona import persona
from app.llm.token_counter import count_tokens
from app.llm.output_parser import model_text, parse_model_json, replace_invalid_unicode, safe_parse_json
from app.system_config import ChatConfig
from app.prompt_loader import load_prompt


DEFAULT_COMPRESSION_TOKENS = ChatConfig().history_compression_tokens
DEFAULT_ANALYSIS = {'intent': '一般对话', 'emotion': '不确定', 'pace': '简短', 'answer': '日常互动'}
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


# 截取送入提示词的短期原话滑动窗口。
def _slice_window(memorier: MemoryManager, recent_window_rounds: int | None = None) -> list[dict]:
    rows = memorier.history_cache
    if recent_window_rounds is not None and recent_window_rounds > 0:
        return rows[-recent_window_rounds:]
    return rows


def _context_history(memorier: MemoryManager, recent_window_rounds: int | None = None) -> str:
    return format_history_dialogue(_slice_window(memorier, recent_window_rounds), bot_name=persona.BOT_NAME)


# 启用情景记忆库时优先注入按需混合召回的事件卡片，否则回退旧版摘要字段。
def _memory_context(state: AIChatState, memorier: MemoryManager) -> str:
    recalled = state.get('recalled_episodes')
    if recalled is not None:
        return recalled or '无'
    return memorier.abstract_cache or '无'


# 基础结点定义
# 定义开始结点
def start_node(state: AIChatState, memorier: MemoryManager, agents, *,
               compression_tokens: int = DEFAULT_COMPRESSION_TOKENS,
               episodes=None,
               recent_window_rounds: int | None = None) -> AIChatState:
    try:
        check_cancelled()
        logger.info('执行结点"start_node"...')
        state['start_time'] = time()

        # 清空上轮的回复
        state['ai_response'] = []
        state['saved'] = False
        state['total_tokens'] = 0
        state['recalled_episodes'] = None
        memorier.refresh_if_needed()

        state['message'] = {
            'conversation_round': memorier.next_round,
            'user': '',
            'assistant': [],
        }

        if episodes is not None:
            window = _slice_window(memorier, recent_window_rounds)
            window_start = window[0]['conversation_round'] if window else None
            query = (state.get('user_input') or '').strip()
            # 用户只回「那后来呢」「为什么」等短句时，拼上上一轮末尾输入补足检索语境。
            if len(query) < 12 and window:
                query = f"{window[-1]['user'][-200:]}\n{query}".strip()
            state['recalled_episodes'] = episodes.recall(
                memorier.user_id, query, exclude_from_round=window_start,
            )
        else:
            # 兼容未启用情景事件库的旧测试路径。
            res = []
            o = 0
            for msg in memorier.history_cache:
                o += 1
                res.append(msg['user'])
                res.extend(msg['assistant'])
            history_text = memorier.abstract_cache + ','.join(res)
            t = count_tokens(history_text) if history_text else 0
            t += 4 * o

            if memorier.has_older_history or t > compression_tokens:
                _, summary_agent, _ = agents
                memorier.compress_history(summary_agent)
                check_cancelled()

        logger.info('结点"start_node"执行完成！')
        return state
    except ChatCancelled:
        raise
    except Exception as error:
        logger.error('start node failed: %s', type(error).__name__)
        raise


# 定义结束结点
def end_node(state: AIChatState, memorier: MemoryManager, _agents) -> AIChatState:
    check_cancelled()
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
def analysis(state: AIChatState, memorier: MemoryManager, agents, *,
             recent_window_rounds: int | None = None) -> AIChatState:
    try:
        check_cancelled()
        logger.info('执行结点"analysis"...')
        user_input = state.get('user_input')

        # 构建分析提示
        analysis_prompt = load_prompt(
            "analysis_prompt",
            user_portrait=state.get('user_portrait') or '暂无',
            history=_context_history(memorier, recent_window_rounds),
            abstract=_memory_context(state, memorier),
            user_input=user_input
        )

        analysis_agent, _, _ = agents
        response = analysis_agent.invoke(
            {
                "messages": [{"role": "user", "content": analysis_prompt}]
            }
        )
        check_cancelled()
        last_message = get_last_message(response)

        # 提取出回复并转成字典格式（使用容错解析）
        defaults = DEFAULT_ANALYSIS
        ai_response = safe_parse_json(last_message.content, defaults, expected_keys=defaults.keys(), stage='消息分析')

        state['analysis_result'] = {}
        for field, default in defaults.items():
            value = ai_response.get(field)
            state['analysis_result'][field] = replace_invalid_unicode(value) if isinstance(value, str) else default
        state['total_tokens'] += get_total_tokens(last_message)

        logger.info('结点"analysis"执行完成！')
        return state
    except ChatCancelled:
        raise
    except Exception as error:
        logger.error('analysis node failed: %s', type(error).__name__)
        raise


# 生成回复结点
def generate(state: AIChatState, memorier: MemoryManager, agents, *,
             recent_window_rounds: int | None = None) -> AIChatState:
    try:
        check_cancelled()
        logger.info('执行结点"generate"...')
        user_input = state.get('user_input', '')

        answer_prompt = load_prompt(
            "answer_prompt",
            user_portrait=state.get('user_portrait') or '暂无',
            history=_context_history(memorier, recent_window_rounds),
            abstract=_memory_context(state, memorier),
            user_input=user_input,
            intent=state['analysis_result'].get('intent', '一般对话'),
            emotion=state['analysis_result'].get('emotion', '不确定'),
            pace=state['analysis_result'].get('pace', '简短'),
            answer=state['analysis_result'].get('answer', '日常互动')
        )

        _, _, reply_agent = agents
        response = reply_agent.invoke(
            {"messages": [{"role": "user", "content": answer_prompt}]},
            config={"recursion_limit": 6}
        )
        check_cancelled()
        last_message = get_last_message(response)
        text = model_text(last_message.content)
        ai_response = parse_model_json(text, ('reply',))
        # 纯文本可直接显示，疑似损坏的结构化输出用默认回复兜底。
        if ai_response is None:
            structured_field = re.search(r'''[{,]\s*["'“]?(?:reply|emotion|action|inner monologue)["'”]?\s*[:：]''', text)
            if text and not text.startswith(('{', '[', '```')) and not structured_field:
                ai_response = {'reply': [text]}
            else:
                metadata = getattr(last_message, 'response_metadata', None)
                reason = 'invalid_json_or_missing_reply' if text else 'empty_visible_text'
                if isinstance(metadata, dict) and metadata.get('finish_reason') == 'length':
                    reason = 'truncated_output'
                logger.warning('回复生成：模型输出无法解析，使用默认回复 reason=%s', reason)
                ai_response = {'reply': [DEFAULT_REPLY], 'inner monologue': [], 'emotion': '困惑', 'action': ''}

        # 记录回复，情绪、动作
        reply = ai_response.get('reply', [])
        if isinstance(reply, str):
            reply = [reply]
        elif not isinstance(reply, list):
            reply = []
        messages = [replace_invalid_unicode(part) for part in reply if isinstance(part, str) and part.strip()]
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
        state['message']['created_at'] = int(time() * 1000)

        logger.info('结点"generate"执行完成！')
        return state
    except ChatCancelled:
        raise
    except Exception as error:
        logger.error('generate node failed: %s', type(error).__name__)
        raise
