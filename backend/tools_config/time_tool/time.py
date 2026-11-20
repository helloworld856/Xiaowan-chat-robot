from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.account_logging import logger

DEFAULT_TIMEZONE = "Asia/Shanghai"


def get_current_time(timezone: str = DEFAULT_TIMEZONE) -> str:
    logger.info("调用时间查询工具...")
    try:
        now = datetime.now(ZoneInfo(timezone))
        res = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        logger.info("时间查询成功！")
        return res
    except (ZoneInfoNotFoundError, ValueError) as e:
        logger.error(f"时间查询失败:{e}")
        return "无效时区，请使用 Asia/Shanghai 等 IANA 时区名称。"
