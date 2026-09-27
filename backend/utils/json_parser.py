import json
import re
import ast
from log_config import logger


def safe_parse_json(text: str, default_result: dict = None) -> dict:
    """容错解析 JSON，失败时返回默认值。"""
    if default_result is None:
        default_result = {
            "intent": "一般对话",
            "emotion": "无情感波动",
            "answer": "情感性回答"
        }

    if not text or not text.strip():
        return default_result

    def try_parse(candidate: str):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        try:
            py_candidate = candidate.replace('true', 'True').replace('false', 'False').replace('null', 'None')
            result = ast.literal_eval(py_candidate)
            if isinstance(result, dict):
                return result
        except (ValueError, SyntaxError, TypeError):
            pass
        return None

    candidates = [text]
    code_block = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if code_block:
        candidates.append(code_block.group(1))

    braces = re.search(r'\{[\s\S]*\}', text)
    if braces:
        normalized = braces.group(0)
        normalized = normalized.replace('，', ',').replace('：', ':')
        normalized = normalized.replace('“', '"').replace('”', '"')
        candidates.extend((normalized, normalized.replace("'", '"')))

    for candidate in candidates:
        result = try_parse(candidate)
        if result is not None:
            return result

    logger.warning(f'JSON 解析失败，使用默认值。原始文本: {text[:200]}')
    return default_result

