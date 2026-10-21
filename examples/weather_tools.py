"""天气查询工具（外部定义，通过 tools= 传入插件）。

数据源：Open-Meteo 公开 API（免费、无需 key）。
- 地理编码：https://geocoding-api.open-meteo.com/v1/search
- 天气预报：https://api.open-meteo.com/v1/forecast
"""

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Dict, List

WEATHER_TEXT = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴冰雹",
    99: "强雷阵雨伴冰雹",
}


def _weather_text(code: int) -> str:
    """天气码转中文描述。"""
    return WEATHER_TEXT.get(code, "未知天气({0})".format(code))


def _get_json(url: str, timeout: float = 10.0) -> Dict[str, Any]:
    """发起 GET 请求并解析 JSON。"""
    request = urllib.request.Request(url, headers={"User-Agent": "zhkj-llm-function-demo/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _geocode(city: str) -> Dict[str, float]:
    """城市名 -> 经纬度。"""
    query = urllib.parse.urlencode(
        {"name": city, "count": 1, "language": "zh", "format": "json"}
    )
    data = _get_json("https://geocoding-api.open-meteo.com/v1/search?" + query)
    results = data.get("results") or []
    if not results:
        raise ValueError("未找到城市: {0}".format(city))
    first = results[0]
    return {
        "latitude": float(first["latitude"]),
        "longitude": float(first["longitude"]),
        "city_name": first.get("name", city),
    }


def _tomorrow() -> str:
    """明天的日期字符串。"""
    return (date.today() + timedelta(days=1)).isoformat()


def query_weather_forecast(city: str, date_str: str | None = None) -> Dict[str, Any]:
    """查询城市指定日期的天气（含逐小时温度与降水概率）。

    参数：
    - city: 城市名称，例如 杭州
    - date_str: 目标日期 YYYY-MM-DD，留空表示明天
    """
    target_date = date_str or _tomorrow()
    location = _geocode(city)

    query = urllib.parse.urlencode(
        {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "start_date": target_date,
            "end_date": target_date,
        }
    )
    data = _get_json("https://api.open-meteo.com/v1/forecast?" + query)

    daily = data.get("daily") or {}
    hourly_raw = data.get("hourly") or {}

    hourly: List[Dict[str, Any]] = []
    times = hourly_raw.get("time") or []
    for index, moment in enumerate(times):
        code = int((hourly_raw.get("weather_code") or [0] * len(times))[index])
        hourly.append(
            {
                "time": moment,
                "temperature": (hourly_raw.get("temperature_2m") or [0.0] * len(times))[index],
                "precipitation_probability": (
                    hourly_raw.get("precipitation_probability") or [0] * len(times)
                )[index],
                "weather_code": code,
                "weather_text": _weather_text(code),
            }
        )

    daily_code = int((daily.get("weather_code") or [0])[0])
    return {
        "city": location["city_name"],
        "date": target_date,
        "weather_code": daily_code,
        "weather_text": _weather_text(daily_code),
        "temperature_max": (daily.get("temperature_2m_max") or [0.0])[0],
        "temperature_min": (daily.get("temperature_2m_min") or [0.0])[0],
        "precipitation_probability_max": (
            daily.get("precipitation_probability_max") or [0]
        )[0] / 100.0,
        "hourly": hourly,
    }


def build_weather_tools() -> List[Dict[str, Any]]:
    """构造可传给插件的工具列表（参数 schema 由框架自动反射）。"""
    return [
        {
            "name": "query_weather_forecast",
            "description": "查询指定城市在指定日期的天气，返回当日天气、最高/最低气温、最大降水概率与逐小时预报",
            "func": query_weather_forecast,
        }
    ]
