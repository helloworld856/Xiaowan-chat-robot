READ_URL_SPEC = {
    "type": "function",
    "function": {
        "name": "read_url",
        "description": (
            "读取用户发来的公网 HTTP/HTTPS 网页链接，提取网页标题、简介与正文文字摘要。"
            "仅当消息中包含明确网址且需要了解链接内容时调用；不支持需要登录的私密页面、音视频或二进制安装包。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "完整的 http:// 或 https:// 网页链接。",
                },
            },
            "required": ["url"],
        },
    },
}
