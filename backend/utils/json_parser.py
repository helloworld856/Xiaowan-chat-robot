import json
import re
import ast
from log_config import logger


def safe_parse_json(text: str, default_result: dict = None) -> dict:
    """
    安全解析 JSON，支持多种容错处理（包括单引号、中文标点、代码块等）

    Args:
        text: 可能包含 JSON 的文本
        default_result: 解析失败时返回的默认值

    Returns:
        解析后的字典，解析失败返回默认值
    """
    if default_result is None:
        default_result = {
            "intent": "一般对话",
            "emotion": "无情感波动",
            "answer": "情感性回答"
        }

    if not text or not text.strip():
        return default_result

    # 辅助函数：尝试多种解析方式
    def try_parse(candidate: str):
        # 方式1: 标准 JSON 解析
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # 方式2: Python 字面量解析（支持单引号、True/False/None）
        try:
            # 将 JSON 的 true/false/null 映射为 Python 字面量
            py_candidate = candidate.replace('true', 'True').replace('false', 'False').replace('null', 'None')
            result = ast.literal_eval(py_candidate)
            if isinstance(result, dict):
                return result
        except (ValueError, SyntaxError, TypeError):
            pass

        return None

    # 1. 直接解析原始文本
    result = try_parse(text)
    if result is not None:
        return result

    # 2. 提取 ```json ... ``` 代码块
    code_block_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    match = re.search(code_block_pattern, text)
    if match:
        result = try_parse(match.group(1))
        if result is not None:
            return result

    # 3. 提取花括号内容
    brace_pattern = r'\{[\s\S]*\}'
    match = re.search(brace_pattern, text)
    if match:
        json_str = match.group(0)

        # 修复常见中文标点
        json_str = json_str.replace('，', ',').replace('：', ':')
        json_str = json_str.replace('“', '"').replace('”', '"')

        # 尝试解析修复后的字符串
        result = try_parse(json_str)
        if result is not None:
            return result

        # 4. 如果仍然失败，尝试更精确的单引号替换（仅替换键和字符串值的单引号，避免破坏内容中的单引号）
        # 使用正则：匹配单引号包裹的键或值，且不是转义的单引号
        # 注意：这个正则简单覆盖大多数情况，但非完美；通常 ast.literal_eval 已经足够
        def fix_single_quotes(s: str) -> str:
            # 匹配形如 'key' : 或 : 'value' 或 'value' , 或整个字符串 '...'
            # 避免替换转义的单引号 \'
            # 简单做法：将前面没有反斜杠的单引号替换为双引号
            # 使用负向后顾断言 (?<!\\) 匹配非转义的单引号
            # 但 Python re 不支持变长后顾，这里用替代方案：逐个字符处理或使用更简单但可能过度的替换
            # 鉴于 ast.literal_eval 通常已能处理，此分支作为最后尝试，可用全局替换（风险已知）
            # 如果你能接受极低概率破坏内部单引号，可直接 replace("'", '"')
            return s.replace("'", '"')  # 简单但非最安全，但作为最后尝试，多数场景可行

        json_str_fixed = fix_single_quotes(json_str)
        result = try_parse(json_str_fixed)
        if result is not None:
            return result

    logger.warning(f'JSON 解析失败，使用默认值。原始文本: {text[:200]}')
    return default_result

