import re
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.account_logging import log_event, logger

DEFAULT_TIMEZONE = "Asia/Shanghai"
TimeOperation = Literal["now", "add_days", "days_between", "convert_timezone"]
WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
DATETIME_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?)?"
)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("无效时区，请使用 Asia/Shanghai 等 IANA 时区名称。") from None


def _parse_date(value: str | None, field: str) -> date:
    if value is not None and DATE_PATTERN.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"请为 {field} 提供 YYYY-MM-DD 格式的有效日期。")


def _parse_datetime(value: str | None) -> datetime:
    if value is not None and DATETIME_PATTERN.fullmatch(value):
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("请为 local_datetime 提供有效日期和时刻，例如 2024-11-03T01:30:00。")


def _resolve_local_time(local: datetime, source: ZoneInfo) -> datetime:
    wall_time = local.replace(tzinfo=None)
    utc = datetime_timezone.utc
    instants = set()
    for fold in (0, 1):
        # 转成 UTC 后再往返检查，区分正常时刻、跳过的时刻与重复的时刻。
        instant = wall_time.replace(tzinfo=source, fold=fold).astimezone(utc)
        if instant.astimezone(source).replace(tzinfo=None) == wall_time:
            instants.add(instant)
    if not instants:
        raise ValueError("该本地时间在来源时区不存在，请核对日期和时刻。")
    if local.tzinfo is not None:
        instant = local.astimezone(utc)
        if instant not in instants:
            raise ValueError("输入的 UTC 偏移量与来源时区在该时刻的偏移量不匹配。")
        return instant
    if len(instants) > 1:
        candidates = " 或 ".join(
            instant.astimezone(source).isoformat(timespec="seconds") for instant in sorted(instants)
        )
        raise ValueError(f"该本地时间在来源时区出现两次，请确认 UTC 偏移量：{candidates}。")
    return instants.pop()


def get_current_time(
    timezone: str = DEFAULT_TIMEZONE,
    *,
    operation: TimeOperation = "now",
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,
    local_datetime: str | None = None,
    target_timezone: str | None = None,
) -> str:
    logger.info("调用时间工具：%s", operation)
    try:
        if operation == "now":
            return datetime.now(_zone(timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")
        if operation == "convert_timezone":
            if target_timezone is None:
                raise ValueError("请提供目标时区 target_timezone，例如 America/New_York。")
            local = _parse_datetime(local_datetime)
            instant = _resolve_local_time(local, _zone(timezone))
            converted = instant.astimezone(_zone(target_timezone))
            return f"{converted.isoformat(sep=' ', timespec='seconds')}（{target_timezone}）"

        if start_date is None:
            start = datetime.now(_zone(timezone)).date()
        else:
            start = _parse_date(start_date, "start_date")
        if operation == "add_days":
            if days is None:
                raise ValueError("请为 days 提供整数天数，可用负数向前推算。")
            result = start + timedelta(days=days)
            return f"{result.isoformat()}（星期{WEEKDAYS[result.weekday()]}）"

        end = _parse_date(end_date, "end_date")
        return f"{start.isoformat()} 到 {end.isoformat()} 相差 {(end - start).days} 天"
    except ValueError as error:
        log_event('tool_failure', tool='time', reason='invalid_arguments')
        return str(error)
    except OverflowError:
        log_event('tool_failure', tool='time', reason='date_overflow')
        return "计算结果超出支持的日期和时间范围（年份 1–9999）。"
