WEB_SEARCH_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "使用联网搜索引擎查询实时动态、近期新闻、赛事结果、影视作品、商品配置、新梗含义或需要核实的客观事实。"
            "仅在涉及时效性信息、不确定具体事实，或用户明确要求帮忙搜索/查询时调用；日常闲聊、情绪倾诉与已知常识不要调用。"
            "若返回的某条摘要信息不够完整，可再配合 read_url 读取对应链接正文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "精炼的搜索关键词（建议 2 至 6 个核心词，避免直接把整句口语长句原样塞入）。",
                },
                "time_range": {
                    "type": "string",
                    "enum": ["", "day", "week", "month", "year"],
                    "description": "可选时间范围筛选：'' 不限时间（默认），'day' 过去24小时，'week' 过去一周，'month' 过去一月，'year' 过去一年。",
                    "default": "",
                },
            },
            "required": ["query"],
        },
    },
}
