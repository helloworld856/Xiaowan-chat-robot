

WEATHER_SPEC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市当前的气温和相对湿度。",
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