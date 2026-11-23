

WEATHER_SPEC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "用户询问指定城市的当前天气时，查询气温和相对湿度。仅支持当前天气，询问天气预报或历史天气时说明无法提供。",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "要查询的城市名称，例如杭州。用户没给出可确定的城市时，先询问地点。",
                },
            },
            "required": ["city"],
        },
    },
}
