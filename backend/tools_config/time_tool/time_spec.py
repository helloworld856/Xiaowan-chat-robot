from typing import get_args

from .time import DEFAULT_TIMEZONE, TimeOperation

CURRENT_TIME_SPEC = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "查询当前日期和时间、按天推算日期和星期、计算日期间隔或倒计时、换算指定时刻的时区。日期使用公历。",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": list(get_args(TimeOperation)),
                    "description": "now 查询当前时间；add_days 从起始日期加减 days 天；days_between 计算 end_date 减起始日期的天数；convert_timezone 将指定时刻换算到另一时区。",
                    "default": "now",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA 时区，例如 Asia/Shanghai 或 America/New_York。用于查询当前时间、确定今天的日期或作为换算的来源时区。省略时用北京时间。",
                    "default": DEFAULT_TIMEZONE,
                },
                "start_date": {
                    "type": "string",
                    "description": "add_days 和 days_between 的起始日期，格式 YYYY-MM-DD。省略时使用 timezone 所在地的今天。",
                },
                "days": {
                    "type": "integer",
                    "description": "add_days 必填。正数向后推算，负数向前推算，0 用于查询起始日期是星期几。",
                },
                "end_date": {
                    "type": "string",
                    "description": "days_between 必填。格式 YYYY-MM-DD。结果为结束日期减起始日期，同一天返回 0。",
                },
                "local_datetime": {
                    "type": "string",
                    "description": "convert_timezone 必填。来源时区的日期和时刻，例如 2024-11-03T01:30:00。遇到夏令时重复时刻时，提供已确认的 UTC 偏移量，如 2024-11-03T01:30:00-04:00。",
                },
                "target_timezone": {
                    "type": "string",
                    "description": "convert_timezone 必填。目标 IANA 时区，例如 Europe/London。",
                },
            },
            "required": [],
        },
    },
}
