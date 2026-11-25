from logging import WARNING

import requests

from app.account_logging import log_event, logger


def get_weather(city: str) -> str:
    logger.info("调用天气查询工具...")

    city = city.strip()
    if len(city) < 2 or len(city) > 100:
        log_event('tool_failure', tool='weather', reason='invalid_city')
        return "城市名称无效"

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
        country = place.get('country')
        country_suffix = f'，{country.strip()}' if isinstance(country, str) and country.strip() else ''

        stage = 'forecast'
        weather = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m",
                "timezone": "auto",
            },
            timeout=8,
        )
        weather.raise_for_status()
        current = weather.json()["current"]

        result = (
            f"地点：{place['name']}{country_suffix}；"
            f"数据时间：{current['time']}；"
            f"气温：{current['temperature_2m']}°C；"
            f"相对湿度：{current['relative_humidity_2m']}%"
        )
        logger.info("天气查询成功!")
        return result

    except (requests.RequestException, KeyError, ValueError, TypeError) as error:
        response = getattr(error, 'response', None)
        log_event('tool_failure', level=WARNING, tool='weather', stage=stage, error=type(error).__name__,
                  http_status=response.status_code if response is not None else None)
        return "天气服务暂时不可用，请稍后再试。"
