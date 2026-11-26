from .weather import MAX_FORECAST_DAYS


WEATHER_SPEC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "查询指定城市的天气。总会返回当前天气现象、气温、体感温度、相对湿度、风速和降水；"
            f"需要今天或之后的天气时，可同时返回从今天起最多 {MAX_FORECAST_DAYS} 天的每日预报"
            "（天气现象、最低/最高气温和降水概率）。不支持历史天气。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "要查询的城市名称，例如杭州。用户没给出可确定的城市时，先询问地点。",
                },
                "days": {
                    "type": "integer",
                    "description": (
                        f"每日预报天数，0 到 {MAX_FORECAST_DAYS}，从今天算起。只问现在的天气时填 0；"
                        "问今天最高温或今天会不会下雨填 1；问明天填 2；问这周或未来几天按需要填写。"
                    ),
                    "default": 0,
                    "minimum": 0,
                    "maximum": MAX_FORECAST_DAYS,
                },
            },
            "required": ["city"],
        },
    },
}
