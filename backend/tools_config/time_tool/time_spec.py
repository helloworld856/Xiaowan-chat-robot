from .time import DEFAULT_TIMEZONE

CURRENT_TIME_SPEC = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "查询当前日期和时间。需要查询现在几点、今天几号或某地当前时间时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA 时区名称，例如 Asia/Shanghai 或 America/New_York。未指定时按北京时间查询。",
                    "default": DEFAULT_TIMEZONE,
                },
            },
            "required": [],
        },
    },
}
