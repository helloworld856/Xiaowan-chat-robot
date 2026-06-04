import json
import re
from log_config import logger


def safe_parse_json(text: str, default_result: dict = None) -> dict:
    """
    安全解析 JSON，支持多种容错处理
    
    Args:
        text: 可能包含 JSON 的文本
        default_result: 解析失败时返回的默认值，若不提供则使用内置默认值
        
    Returns:
        解析后的字典，解析失败返回默认值
    """
    # 默认返回值
    if default_result is None:
        default_result = {
            "intent": "一般对话",
            "emotion": "无情感波动",
            "answer": "情感性回答"
        }
    
    if not text or not text.strip():
        return default_result

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 尝试提取 JSON 代码块 (```json ... ``` 或 ``` ... ```)
    code_block_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    match = re.search(code_block_pattern, text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    
    # 尝试提取花括号内容
    brace_pattern = r'\{[\s\S]*\}'
    match = re.search(brace_pattern, text)
    if match:
        try:
            # 修复常见的 JSON 格式问题
            json_str = match.group(0)
            # 替换中文标点
            json_str = json_str.replace('，', ',').replace('：', ':').replace('"', '"').replace('"', '"')
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    # 所有方法都失败，返回默认值
    logger.warning(f'JSON 解析失败，使用默认值。原始文本: {text}')
    return default_result

