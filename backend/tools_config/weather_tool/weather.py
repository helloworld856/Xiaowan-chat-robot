from logging import WARNING
from math import isfinite

import requests

from app.account_logging import log_event, logger


MAX_FORECAST_DAYS = 7

# Open-Meteo 使用 WMO 天气代码，这里换成日常说法。
WEATHER_CODES = {
    0: '晴', 1: '大致晴朗', 2: '多云', 3: '阴',
    45: '雾', 48: '冻雾',
    51: '小毛毛雨', 53: '毛毛雨', 55: '大毛毛雨', 56: '冻毛毛雨', 57: '强冻毛毛雨',
    61: '小雨', 63: '中雨', 65: '大雨', 66: '冻雨', 67: '强冻雨',
    71: '小雪', 73: '中雪', 75: '大雪', 77: '米雪',
    80: '小阵雨', 81: '阵雨', 82: '强阵雨', 85: '小阵雪', 86: '强阵雪',
    95: '雷阵雨', 96: '雷阵雨伴小冰雹', 99: '雷阵雨伴大冰雹',
}

CURRENT_FIELDS = 'temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation'
DAILY_FIELDS = 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max'


def _number(value) -> bool:
    return type(value) in (int, float) and isfinite(value)


def describe_weather_code(code) -> str | None:
    return WEATHER_CODES.get(code) if type(code) is int else None


def _format_current(current: dict) -> list[str]:
    observed_at = current['time']
    temperature = current['temperature_2m']
    humidity = current['relative_humidity_2m']
    if not isinstance(observed_at, str) or not observed_at.strip() or not all(map(_number, (temperature, humidity))):
        raise ValueError('当前天气数据无效')

    parts = [f"数据时间：{observed_at}"]
    condition = describe_weather_code(current.get('weather_code'))
    if condition:
        parts.append(f"天气：{condition}")
    parts.append(f"气温：{temperature}°C")
    # 可选字段缺失或无效时省略，不影响主要结果。
    apparent = current.get('apparent_temperature')
    if _number(apparent):
        parts.append(f"体感：{apparent}°C")
    parts.append(f"相对湿度：{humidity}%")
    wind = current.get('wind_speed_10m')
    if _number(wind):
        parts.append(f"风速：{wind} km/h")
    precipitation = current.get('precipitation')
    if _number(precipitation):
        parts.append(f"当前降水：{precipitation} mm")
    return parts


def _format_daily(daily: dict, days: int) -> str:
    dates = daily['time']
    codes = daily['weather_code']
    highs = daily['temperature_2m_max']
    lows = daily['temperature_2m_min']
    chances = daily.get('precipitation_probability_max') or [None] * len(dates)
    columns = (dates, codes, highs, lows, chances)
    if not all(isinstance(column, list) for column in columns) or len(dates) < days:
        raise ValueError('预报数据无效')
    if any(len(column) < days for column in columns):
        raise ValueError('预报数据长度不一致')

    lines = []
    for index in range(days):
        date, high, low = dates[index], highs[index], lows[index]
        if not isinstance(date, str) or not _number(high) or not _number(low):
            raise ValueError('预报数据无效')
        item = f"{date} {describe_weather_code(codes[index]) or '天气未知'}，{low}~{high}°C"
        if _number(chances[index]):
            item += f"，降水概率 {chances[index]}%"
        lines.append(item)
    return '；'.join(lines)


def get_weather(city: str, days: int = 0) -> str:
    logger.info("调用天气查询工具...")

    city = city.strip()
    if len(city) < 2 or len(city) > 100:
        log_event('tool_failure', tool='weather', reason='invalid_city')
        return "城市名称无效"
    if days < 0 or days > MAX_FORECAST_DAYS:
        log_event('tool_failure', tool='weather', reason='invalid_days')
        return f"预报天数须在 0 到 {MAX_FORECAST_DAYS} 之间"

    stage = 'geocoding'
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "zh"},
            timeout=8,
        )
        geo.raise_for_status()
        locations = geo.json()
        if not isinstance(locations, dict) or locations.get('error'):
            raise ValueError('地理编码响应无效')
        places = locations.get('results', [])
        if not isinstance(places, list):
            raise ValueError('地理编码结果无效')

        if not places:
            log_event('tool_failure', tool='weather', stage=stage, reason='city_not_found')
            return f"找不到城市：{city}"

        place = places[0]
        if not isinstance(place, dict):
            raise ValueError('城市记录无效')
        name = place['name']
        if not isinstance(name, str) or not name.strip():
            raise ValueError('城市名称无效')
        country = place.get('country')
        country_suffix = f'，{country.strip()}' if isinstance(country, str) and country.strip() else ''

        stage = 'forecast'
        params = {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": CURRENT_FIELDS,
            "timezone": "auto",
        }
        if days:
            params.update(daily=DAILY_FIELDS, forecast_days=days)
        weather = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=8)
        weather.raise_for_status()
        payload = weather.json()
        parts = [f"地点：{name}{country_suffix}", *_format_current(payload["current"])]
        if days:
            parts.append(f"未来 {days} 天预报：{_format_daily(payload['daily'], days)}")

        logger.info("天气查询成功!")
        return '；'.join(parts)

    except (requests.RequestException, KeyError, ValueError, TypeError, OverflowError) as error:
        response = getattr(error, 'response', None)
        log_event('tool_failure', level=WARNING, tool='weather', stage=stage, error=type(error).__name__,
                  http_status=response.status_code if response is not None else None)
        return "天气服务暂时不可用，请稍后再试。"
