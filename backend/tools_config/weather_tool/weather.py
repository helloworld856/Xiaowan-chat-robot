from app.account_logging import logger
import requests


def get_weather(city: str) -> str:
    logger.info("调用天气查询工具...")

    city = city.strip()
    if len(city) < 2 or len(city) > 100:
        return "城市名称无效"

    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "zh"},
            timeout=8,
        )
        geo.raise_for_status()
        places = geo.json().get("results") or []

        if not places:
            return f"找不到城市：{city}"

        place = places[0]

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

        logger.info("天气查询成功!")
        return (
            f"地点：{place['name']}，{place.get('country', '')}；"
            f"数据时间：{current['time']}；"
            f"气温：{current['temperature_2m']}°C；"
            f"相对湿度：{current['relative_humidity_2m']}%"
        )

    except (requests.RequestException, KeyError, ValueError, TypeError) as e:
        logger.error(f"天气查询失败:{e}")
        return "天气服务暂时不可用，请稍后再试。"
