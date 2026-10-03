import ast
import json
import re
import tokenize
from copy import deepcopy
from io import StringIO
from typing import Optional

from log_config import logger


def safe_parse_json(text: str, default_result: Optional[dict] = None) -> dict:
    """容错解析 JSON，失败时返回默认值。"""
    if default_result is None:
        default_result = {
            "intent": "一般对话",
            "emotion": "无情感波动",
            "answer": "情感性回答"
        }

    if not text or not text.strip():
        return deepcopy(default_result)

    def try_parse(candidate: str):
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
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
        except (ValueError, SyntaxError, TypeError, tokenize.TokenError):
            pass
        return None

    candidates = [text]
    code_block = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if code_block:
        candidates.append(code_block.group(1))

    braces = re.search(r'\{[\s\S]*\}', text)
    if braces:
        normalized = braces.group(0)
        candidates.append(normalized)
        normalized = normalized.replace('，', ',').replace('：', ':')
        normalized = normalized.replace('“', '"').replace('”', '"')
        candidates.extend((normalized, normalized.replace("'", '"')))

    for candidate in candidates:
        result = try_parse(candidate)
        if result is not None:
            return result

    logger.warning(f'JSON 解析失败，使用默认值。原始文本: {text[:200]}')
    return deepcopy(default_result)

