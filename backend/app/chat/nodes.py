import re
from collections.abc import Callable
from datetime import datetime
from time import time
from zoneinfo import ZoneInfo

from app.account_logging import logger
from app.chat.memory import MemoryManager, format_history_dialogue
from app.chat.progress import ChatCancelled, check_cancelled
from app.chat.state import AIChatState
from app.chat.tool_context import current_tool_context
from app.llm.output_parser import (
    ReplyBubbleStreamParser,
    chunk_visible_text,
    model_text,
    parse_model_json,
    replace_invalid_unicode,
)
from app.llm.persona import persona
from app.llm.token_counter import count_tokens
from app.prompt_loader import load_prompt
from app.system_config import ChatConfig


DEFAULT_COMPRESSION_TOKENS = ChatConfig().history_compression_tokens
DEFAULT_REPLY = '嗯...我刚刚有点没组织好语言，你再说一遍嘛'
_SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
_WEEKDAYS = ('星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日')
_JSON_RECOVERY_PROMPT = '请基于以上对话与工具结果，直接输出标准 JSON 对象 {"reply":["..."]} 自然回复对方，不要返回空内容。'
_DIRECT_JSON_RECOVERY_PROMPT = '请直接输出标准 JSON 对象 {"reply":["..."]} 自然回复对方，不要返回空内容。'
_MAX_EMPTY_OUTPUT_RETRIES = 2


def _current_beijing_time() -> str:
    now = datetime.now(_SHANGHAI_TZ)
    return f"{now.strftime('%Y-%m-%d %H:%M')}（{_WEEKDAYS[now.weekday()]}）"


def unpack_agents(agents):
    """兼容 (summary, reply) 与旧测试传入的 (analysis, summary, reply)。"""
    if len(agents) == 2:
        return agents[0], agents[1]
    if len(agents) == 3:
        return agents[1], agents[2]
    raise ValueError(f'unexpected agents tuple length: {len(agents)}')


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

        window = _slice_window(memorier, recent_window_rounds)
        window_start = window[0]['conversation_round'] if window else memorier.next_round
        tool_ctx = current_tool_context()
        if tool_ctx is not None:
            tool_ctx.window_start_round = window_start

        if episodes is not None:
            ep_window_start = window[0]['conversation_round'] if window else None
            query = (state.get('user_input') or '').strip()
            # 用户只回「那后来呢」「为什么」等短句时，拼上上一轮末尾输入补足检索语境。
            if len(query) < 12 and window:
                query = f"{window[-1]['user'][-200:]}\n{query}".strip()
            state['recalled_episodes'] = episodes.recall(
                memorier.user_id, query, exclude_from_round=ep_window_start,
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
                summary_agent, _ = unpack_agents(agents)
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

    logger.info('本轮对话 %d：回复 %d 条，回复 Token %s，耗时 %.2f 秒，保存%s',
                state['message']['conversation_round'] + 1, len(state['ai_response']),
                state['total_tokens'] or '未返回', time() - state['start_time'],
                '成功' if state['saved'] else '失败')

    return state


def _is_assistant_chunk(chunk) -> bool:
    msg_type = getattr(chunk, 'type', '')
    return msg_type in ('AIMessageChunk', 'ai')


def _is_tool_chunk(chunk) -> bool:
    msg_type = getattr(chunk, 'type', '')
    return msg_type in ('ToolMessage', 'ToolMessageChunk', 'tool')


def _append_recovery_nudge(user_content: str | list[dict], nudge: str) -> str | list[dict]:
    if isinstance(user_content, str):
        return f'{user_content}\n\n{nudge}'
    copied = [dict(block) for block in user_content]
    for idx in range(len(copied) - 1, -1, -1):
        if copied[idx].get('type') == 'text' and isinstance(copied[idx].get('text'), str):
            copied[idx]['text'] = f"{copied[idx]['text']}\n\n{nudge}"
            return copied
    copied.append({'type': 'text', 'text': nudge})
    return copied


def _strip_trailing_empty_ai(messages: list) -> list:
    trimmed = list(messages)
    while trimmed:
        last = trimmed[-1]
        msg_type = getattr(last, 'type', '') if not isinstance(last, dict) else last.get('role', '')
        if msg_type not in ('ai', 'AIMessage', 'assistant'):
            break
        has_tool_calls = (
            bool(getattr(last, 'tool_calls', None))
            if not isinstance(last, dict)
            else bool(last.get('tool_calls'))
        )
        raw = getattr(last, 'content', '') if not isinstance(last, dict) else last.get('content', '')
        if has_tool_calls or model_text(raw):
            break
        trimmed.pop()
    return trimmed


def _stream_reply(reply_agent, user_content: str | list[dict], on_bubble: Callable[[str], None]):
    """流式调用回复代理，每闭合一条气泡立即回调，并在偶发空输出时自动补救。"""
    parser = ReplyBubbleStreamParser()
    total_tokens = 0
    finish_reason = None
    saw_tool_message = False
    state_messages: list = []

    def consume_stream(input_messages: list):
        nonlocal total_tokens, finish_reason, saw_tool_message, state_messages
        pass_tokens = 0
        stream_iter = reply_agent.stream(
            {"messages": input_messages},
            config={"recursion_limit": 10},
            stream_mode=["messages", "values"],
        )
        for raw_event in stream_iter:
            check_cancelled()
            # 兼容真实代理的 (mode, data) 与单测假代理直接产出的 (chunk, meta)。
            if isinstance(raw_event, tuple) and len(raw_event) == 2 and raw_event[0] in ('messages', 'values'):
                mode, data = raw_event
                if mode == 'values':
                    if isinstance(data, dict) and isinstance(data.get('messages'), list):
                        state_messages = data['messages']
                    continue
                chunk = data[0] if isinstance(data, tuple) else data
            else:
                chunk = raw_event[0] if isinstance(raw_event, tuple) else raw_event

            if _is_tool_chunk(chunk):
                saw_tool_message = True
                parser.advance_after_tool()
                continue
            if not _is_assistant_chunk(chunk):
                continue

            tokens = get_total_tokens(chunk)
            if tokens > 0:
                pass_tokens = tokens
            metadata = getattr(chunk, 'response_metadata', None)
            if isinstance(metadata, dict) and metadata.get('finish_reason'):
                finish_reason = metadata['finish_reason']

            piece = chunk_visible_text(getattr(chunk, 'content', ''))
            if piece:
                for bubble in parser.feed(piece):
                    on_bubble(bubble)
        total_tokens += pass_tokens

    initial_messages = [{"role": "user", "content": user_content}]
    consume_stream(initial_messages)

    # 开启 JSON 模式与推理的模型（含视觉输入或工具返回后）偶发在推理结束后产出空正文，保持前缀缓存轻量重试。
    attempt = 0
    while (
        attempt < _MAX_EMPTY_OUTPUT_RETRIES
        and not parser.raw_text.strip()
        and not parser.prior_turn_bubbles
        and finish_reason != 'length'
    ):
        attempt += 1
        logger.info('模型返回空文本，自动重试生成 (%d/%d)...', attempt, _MAX_EMPTY_OUTPUT_RETRIES)
        parser.reset()
        finish_reason = None
        if saw_tool_message:
            base_msgs = _strip_trailing_empty_ai(state_messages) if state_messages else list(initial_messages)
            retry_messages = base_msgs + [{"role": "user", "content": _JSON_RECOVERY_PROMPT}]
        else:
            nudged_content = _append_recovery_nudge(user_content, _DIRECT_JSON_RECOVERY_PROMPT)
            retry_messages = [{"role": "user", "content": nudged_content}]
        consume_stream(retry_messages)

    return parser.raw_text, total_tokens, finish_reason, parser.prior_turn_bubbles, parser.emitted


def _compose_multimodal_prompt_input(user_input: str, turn_images: list[str], turn_documents: list[dict]) -> str:
    stripped = user_input.strip()
    if stripped:
        base = user_input
    elif turn_images and turn_documents:
        base = '[对方发来了图片与文档，未附额外文字]'
    elif turn_images:
        base = '[对方发来了图片，未附额外文字]'
    elif turn_documents:
        base = '[对方发来了文档，未附额外文字]'
    else:
        base = user_input

    if not turn_documents:
        return base

    doc_blocks = []
    for doc in turn_documents:
        label = '引用的文档' if doc.get('quoted') else '附带的文档'
        suffix = '（内容较长已截取前段）' if doc.get('truncated') else ''
        doc_blocks.append(f"【{label}：{doc['name']}{suffix}】\n{doc['text']}")
    return base + '\n\n' + '\n\n'.join(doc_blocks)


# 生成回复结点
def generate(state: AIChatState, memorier: MemoryManager, agents, *,
             recent_window_rounds: int | None = None,
             on_bubble: Callable[[str], None] | None = None) -> AIChatState:
    try:
        check_cancelled()
        logger.info('执行结点"generate"...')
        user_input = state.get('user_input') or ''
        turn_images = list(state.get('turn_images') or ())
        turn_documents = list(state.get('turn_documents') or ())
        prompt_user_input = _compose_multimodal_prompt_input(user_input, turn_images, turn_documents)

        answer_prompt = load_prompt(
            "answer_prompt",
            current_time=_current_beijing_time(),
            user_portrait=state.get('user_portrait') or '暂无',
            history=_context_history(memorier, recent_window_rounds),
            abstract=_memory_context(state, memorier),
            user_input=prompt_user_input,
        )
        user_content: str | list[dict] = (
            [{"type": "image_url", "image_url": {"url": data_url}} for data_url in turn_images]
            + [{"type": "text", "text": answer_prompt}]
            if turn_images else answer_prompt
        )

        _, reply_agent = unpack_agents(agents)
        prior_bubbles: list[str] = []
        emitted: list[str] = []
        finish_reason = None
        if on_bubble is not None and callable(getattr(reply_agent, 'stream', None)):
            raw_content, tokens, finish_reason, prior_bubbles, emitted = _stream_reply(
                reply_agent, user_content, on_bubble
            )
            text = model_text(raw_content)
        else:
            response = reply_agent.invoke(
                {"messages": [{"role": "user", "content": user_content}]},
                config={"recursion_limit": 10}
            )
            check_cancelled()
            last_message = get_last_message(response)
            raw_last = chunk_visible_text(last_message.content)
            text = model_text(last_message.content)
            tokens = get_total_tokens(last_message)
            metadata = getattr(last_message, 'response_metadata', None)
            if isinstance(metadata, dict):
                finish_reason = metadata.get('finish_reason')
            if not raw_last.strip() and finish_reason != 'length':
                logger.info('模型返回空文本，自动重试生成 (1/1)...')
                if len(response.get('messages', ())) > 2:
                    retry_messages = _strip_trailing_empty_ai(response['messages']) + [
                        {"role": "user", "content": _JSON_RECOVERY_PROMPT}
                    ]
                else:
                    retry_messages = [
                        {"role": "user", "content": _append_recovery_nudge(user_content, _DIRECT_JSON_RECOVERY_PROMPT)}
                    ]
                retry_response = reply_agent.invoke({"messages": retry_messages}, config={"recursion_limit": 4})
                check_cancelled()
                retry_last = get_last_message(retry_response)
                text = model_text(retry_last.content)
                tokens += get_total_tokens(retry_last)
                retry_meta = getattr(retry_last, 'response_metadata', None)
                if isinstance(retry_meta, dict):
                    finish_reason = retry_meta.get('finish_reason')

        ai_response = parse_model_json(text, ('reply',))
        # 纯文本可直接显示，疑似损坏的结构化输出用默认回复兜底。
        if ai_response is None:
            structured_field = re.search(r'''[{,]\s*["'“]?(?:reply|emotion|action|inner monologue)["'”]?\s*[:：]''', text)
            if text and not text.startswith(('{', '[', '```')) and not structured_field:
                ai_response = {'reply': [text]}
            else:
                reason = 'invalid_json_or_missing_reply' if text else 'empty_visible_text'
                if finish_reason == 'length':
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
        turn_messages = messages or ([] if prior_bubbles else [DEFAULT_REPLY])
        if on_bubble is not None and len(emitted) < len(turn_messages):
            for bubble in turn_messages[len(emitted):]:
                on_bubble(bubble)
        final_messages = prior_bubbles + turn_messages
        state['ai_response'] = final_messages

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

        state['total_tokens'] += tokens

        # 记录对话
        state['message']['user'] = user_input
        state['message']['assistant'] = state['ai_response']
        state['message']['created_at'] = int(time() * 1000)
        turn_attachments = state.get('attachments') or []
        if turn_attachments:
            state['message']['attachments'] = turn_attachments
        else:
            state['message'].pop('attachments', None)

        logger.info('结点"generate"执行完成！')
        return state
    except ChatCancelled:
        raise
    except Exception as error:
        logger.error('generate node failed: %s', type(error).__name__)
        raise
