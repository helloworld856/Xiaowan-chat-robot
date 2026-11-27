import ast
import json
import re
import tokenize
from collections.abc import Collection
from copy import deepcopy
from io import StringIO
from typing import Optional

from app.account_logging import logger


def replace_invalid_unicode(text: str) -> str:
    # 容错解码会保留缺少配对的代理码点，无法直接写入 UTF-8。
    if re.search(r'[\ud800-\udfff]', text):
        return text.encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'replace')
    return text


def model_text(content: object) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str):
                parts.append(block['text'])
        text = ''.join(parts)
    else:
        return ''
    # 只保留可见正文，起始处未闭合的推理块也会去除。
    visible = re.sub(r'^\s*(?:<think>.*?(?:</think>|$)\s*)+', '', text, flags=re.IGNORECASE | re.DOTALL)
    return replace_invalid_unicode(visible.strip())


def parse_model_json(text: str, expected_keys: Collection[str] = ()) -> dict | None:
    def try_parse(candidate: str):
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except (ValueError, RecursionError):
            pass

        try:
            # 只转换 JSON 常量，保留引号内的原文。
            constants = {'true': 'True', 'false': 'False', 'null': 'None'}
            py_candidate = tokenize.untokenize(
                (token.type, constants.get(token.string, token.string)
                 if token.type == tokenize.NAME else token.string)
                for token in tokenize.generate_tokens(StringIO(candidate).readline)
            )
            result = ast.literal_eval(py_candidate)
            if isinstance(result, dict):
                return result
        except (ValueError, SyntaxError, TypeError, RecursionError, tokenize.TokenError):
            pass
        return None

    def matches(result):
        return isinstance(result, dict) and (not expected_keys or not result.keys().isdisjoint(expected_keys))

    candidates = [text]
    candidates.extend(re.findall(r'```(?:json)?\s*(.*?)\s*```', text, re.IGNORECASE | re.DOTALL))
    for candidate in candidates:
        result = try_parse(candidate)
        if matches(result):
            return result

    # raw_decode 只读取当前对象，后面有说明或另一个对象时仍能解析。
    decoder = json.JSONDecoder()
    end = 0
    while (start := text.find('{', end)) != -1:
        try:
            result, end = decoder.raw_decode(text, start)
        except ValueError:
            end = start + 1
            continue
        except RecursionError:
            break
        if matches(result):
            return result

    braces = re.search(r'\{[\s\S]*\}', text)
    if braces:
        normalized = braces.group(0)
        punctuation = normalized.replace('，', ',').replace('：', ':').replace('“', '"').replace('”', '"')
        for candidate in (normalized, punctuation, punctuation.replace("'", '"')):
            result = try_parse(candidate)
            if matches(result):
                return result
    return None


def safe_parse_json(content: object, default_result: Optional[dict] = None, *,
                    expected_keys: Collection[str] = (), stage: str = '模型输出') -> dict:
    """容错解析 JSON，失败时返回默认值。"""
    if default_result is None:
        default_result = {
            "intent": "一般对话",
            "emotion": "无情感波动",
            "answer": "情感性回答"
        }

    text = model_text(content)
    result = parse_model_json(text, expected_keys) if text else None
    if result is not None:
        return result

    logger.warning('%s：未收到可用的 JSON，使用默认值', stage)
    return deepcopy(default_result)
