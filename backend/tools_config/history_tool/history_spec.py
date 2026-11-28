from .history import MAX_KEYWORDS

SEARCH_HISTORY_SPEC = {
    "type": "function",
    "function": {
        "name": "search_chat_history",
        "description": (
            "按关键词或日期范围检索当前账号更早之前的聊天原话（不含已在当前对话窗口中的最近记录）。"
            "当对方问起以前提过的具体店名、书名、人名、数字、链接或某天的原话细节，而「之前聊过的相关事情」摘要卡片里没有记全时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        f"要查找的 1 到 {MAX_KEYWORDS} 个关键词，例如 [\"日料\", \"店名\"] 或 [\"React\", \"面试\"]。"
                        "尽量挑核心名词或专有名词，不要把整句疑问原样塞进来。"
                    ),
                    "default": [],
                },
                "start_date": {
                    "type": "string",
                    "description": "可选起始日期（含当天），格式 YYYY-MM-DD，按北京时间计算。",
                    "default": "",
                },
                "end_date": {
                    "type": "string",
                    "description": "可选结束日期（含当天），格式 YYYY-MM-DD，按北京时间计算。",
                    "default": "",
                },
            },
            "required": [],
        },
    },
}
