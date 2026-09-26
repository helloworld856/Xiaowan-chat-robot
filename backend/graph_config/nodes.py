from state_config import AIChatState
from log_config import logger
from memory_config import memorier
from utils import count_tokens, safe_parse_json
import agent_config  # 改为导入模块，这样可以动态获取最新的代理
from persona_config import persona
from config import configer

from time import time



def get_last_message(response: dict):
    messages = response.get("messages", [])
    if not messages:
        raise ValueError("模型返回结果中没有 messages")
    return messages[-1]


def get_total_tokens(message) -> int:
    metadata = getattr(message, "response_metadata", {}) or {}
    token_usage = metadata.get("token_usage", {}) or {}
    return token_usage.get("total_tokens", 0)


# 基础结点定义
# 定义开始结点
def start_node(state: AIChatState) -> AIChatState:
    try:
        logger.info('执行结点"start_node"...')
        state['start_time'] = time()

        # 清空上轮的回复
        state['ai_response'] = []

        # 计算tokens
        res = []
        o = 0
        for msg in memorier.history_cache:
            o += 1
            res.append(msg['user'])
            res.extend(msg['assistant'])
        t = count_tokens(memorier.abstract_cache + ','.join(res))
        t += 4 * o  # 把每条对话的user,assistant,conversation_round都算上

        if t > configer.compress_history_threshold:
            # 生成历史对话摘要
            memorier.compress_history()

        if not memorier.cache_valid:    # 若压缩过历史对话，加载新的历史对话和摘要
            logger.info('缓存失效，重新加载历史对话和摘要')
            memorier.history_cache = memorier.load_history(0)
            memorier.abstract_cache = memorier.load_abstract()
            memorier.cache_valid = True

        logger.info('结点"start_node"执行完成！')
        return state
    except Exception as e:
        logger.error(f'执行结点"start_node"时出现错误:{e}')
        raise


# 定义结束结点
def end_node(state: AIChatState) -> AIChatState:
    logger.info('执行结点"end_node"...')

    # 将本轮对话写入文件
    memorier.record_message(state['message'])

    logger.info(f'本轮对话:{state["message"]}')
    logger.info(f'\n她的回复:{state["ai_response"]}\n她的内心独白:{state["ai_monologue"]}\n她的情绪:{state["ai_emotion"]}\n她的动作:{state["ai_action"]}')
    logger.info(f'总token使用:{state["total_tokens"]}')
    logger.info(f'执行总用时:{time()-state["start_time"]:.2f}s')
    logger.info('结点"end_node"执行完成！\n' + '-'*65)

    state['message']['conversation_round'] += 1  # 轮次加1
    return state


# AI结点定义
# 分析用户输入
def analysis(state: AIChatState) -> AIChatState:
    try:
        logger.info('执行结点"analysis"...')
        user_input = state.get('user_input')

        # 构建分析提示
        if memorier.abstract_cache:
            analysis_prompt = f"""请根据用户（对应'user'）和{persona.BOT_NAME}（对应'assistant'）的历史对话及更早的历史对话摘要，分析用户输入，直接返回 JSON，不要有其他内容。
历史对话：'{memorier.history_cache}'。更早的历史对话摘要：'{memorier.abstract_cache}'"""
        else:
            analysis_prompt = f"""请根据用户（对应'user'）和{persona.BOT_NAME}（对应'assistant'）的历史对话，分析用户输入，直接返回 JSON，不要有其他内容。
历史对话：'{memorier.history_cache}'"""

        analysis_prompt += f"""用户说："{user_input}"

分析维度：
1. intent（意图）：用户想要什么？

2. emotion（情感）：用户的情感，从以下选择，可多选用逗号分隔
   - 积极：开心、感激、期待、自豪、爱
   - 消极：难过、生气、焦虑、失望、孤独、内疚、恐惧
   - 中性：平静、好奇、疑惑
   - 特殊：无明显情感（仅在无法判断时使用）

3. answer（回复类型）：小晚的回复，从以下选择一个主要类型（必要时可补充次要类型）
   - 情感支持：安慰、鼓励、共情
   - 信息提供：解答问题、提供建议
   - 日常互动：闲聊、打招呼、告别

返回格式：
{{"intent": "...", "emotion": "...", "answer": "..."}}"""

        # 获得回复（动态获取当前代理）
        response = agent_config.analysis_agent.invoke(
            {
                "messages": [{"role": "user", "content": analysis_prompt}]
            }
        )
        last_message = get_last_message(response)

        # 提取出回复并转成字典格式（使用容错解析）
        ai_response = safe_parse_json(last_message.content, configer.ANALYSIS_DEFAULT_RESULT)

        state['analysis_result'] = ai_response
        state['total_tokens'] += get_total_tokens(last_message)

        logger.info(f'分析结果:{state["analysis_result"]}')
        logger.info('结点"analysis"执行完成！')
        return state
    except Exception as e:
        logger.error(f'执行结点"analysis"时出现错误:{e}')
        raise

# 生成回复结点
def generate(state: AIChatState) -> AIChatState:
    try:
        logger.info('执行结点"generate"...')
        user_input = state.get('user_input', '')

        if memorier.abstract_cache:
            user_prompt = f"""【历史对话（用户对应'user'）和{persona.BOT_NAME}对应'assistant'）】{memorier.history_cache}
【更早的历史对话摘要】{memorier.abstract_cache}
---以上是之前的历史对话及更早的历史对话摘要，请保持连贯性---"""
        else:
            user_prompt = f"""【历史对话（用户对应'user'）和{persona.BOT_NAME}对应'assistant'）】{memorier.history_cache}
---以上是之前的历史对话，请保持连贯性---
"""
        # 构建提示词
        user_prompt += f"""【用户说】{user_input}
【分析结果，仅供参考】
- 用户意图：{state['analysis_result'].get('intent', '一般对话')}
- 用户情绪：{state['analysis_result'].get('emotion', '无情绪波动')}
- 建议回复：{state['analysis_result'].get('answer', '日常互动')}

情绪分析和建议回复可能不准确，以用户原话和最近的对话为准，不必把情绪标签说出来。
以你的人设回应用户当前说的事，不必为了符合分析结果而强行安慰、提问或转移话题。
通常回复一条消息；有自然停顿时可以分成两条，不为凑条数拆句。用户认真提问时，按内容需要展开。
请直接返回标准 JSON，所有键名和字符串都使用双引号，不要用代码块包裹，也不要输出额外文字。
回答维度:
1、"reply":你要回复给用户的话（非空字符串列表，每个元素是一条完整消息）。
2、"inner monologue":保留列表字段，默认返回空列表，不需要编写内心独白。
3、"emotion":简短的情绪描述（字符串），没有明显变化时可写“平静”，不必刻意制造情绪。
4、"action":保留字符串字段，默认返回空字符串，不需要编写动作。
优先保证 reply 自然、连贯，不要为了补充其他字段而表演情绪或编造情节。
回复格式:
{{"reply":["这里填写对用户的实际回复"],"inner monologue":[],"emotion":"平静","action":""}}"""

        response = agent_config.friend_agent.invoke(
            {"messages": [{"role": "user", "content": user_prompt}]}
        )
        last_message = get_last_message(response)
        ai_response = safe_parse_json(last_message.content, configer.GENERATE_DEFAULT_RESULT)

        # 记录回复，情绪、动作
        reply = ai_response.get('reply', [])
        if isinstance(reply, str):
            reply = [reply]
        elif not isinstance(reply, list):
            reply = configer.GENERATE_DEFAULT_RESULT["reply"].copy()
        state['ai_response'] = reply
        state['ai_monologue'] = ai_response.get('inner monologue', '')
        state['ai_emotion'] = ai_response.get('emotion', '')
        state['ai_action'] = ai_response.get('action', '')

        state['total_tokens'] += get_total_tokens(last_message)

        # 记录对话
        state['message']['user'] = user_input
        state['message']['assistant'] = state['ai_response']

        logger.info('结点"generate"执行完成！')
        return state
    except Exception as e:
        logger.error(f'结点"generate"执行时出现错误:{e}')
        raise
