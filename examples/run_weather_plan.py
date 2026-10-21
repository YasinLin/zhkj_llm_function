"""示例：结合「明天」的天气规划明天行程。

运行（.venv）：
    .venv\\Scripts\\python.exe examples\\run_weather_plan.py

- 真实模式：设置 LLM_API_KEY（可选 LLM_MODEL / LLM_BASE_URL）后运行，走真实模型 + Open-Meteo 天气 API。
- 离线模式：未设置 LLM_API_KEY 时使用内置假模型与假天气数据，可离线跑通全链路。
"""

import asyncio
import json
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm  # noqa: E402

from zhkj_llm_function import ToolDef  # noqa: E402
from plugins.weather_plan_plugin import WeatherPlanPlugin  # noqa: E402

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


# --------------------------------------------------------------- 离线：假模型 + 假天气
def install_fake_llm(weather: dict):
    """假模型：先要求调用天气工具，再输出结构化 JSON。"""

    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                type="function",
                                function=SimpleNamespace(
                                    name="query_weather_forecast",
                                    arguments=json.dumps(
                                        {"city": "杭州", "date_str": TOMORROW}
                                    ),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "plan_date": TOMORROW,
                                "city": "杭州",
                                "weather_summary": "多云转小雨，午后有雷阵雨",
                                "temperature_range": "19~27℃",
                                "precipitation_probability_max": 0.75,
                                "has_heavy_rain": True,
                                "suggestions": [
                                    "午后降水概率 75%，务必带伞",
                                    "上午多云凉爽，适合户外散步",
                                    "雷阵雨时段避免在户外长时间停留",
                                ],
                                "plan": [
                                    {
                                        "time_range": "08:30-11:30",
                                        "title": "公园散步与晨间咖啡",
                                        "activity_type": "outdoor",
                                        "weather_basis": "降水概率 10%、气温 21℃",
                                        "reason": "上午多云凉爽，降水概率低，适合户外活动",
                                    },
                                    {
                                        "time_range": "12:00-17:00",
                                        "title": "室内商场与咖啡馆",
                                        "activity_type": "indoor",
                                        "weather_basis": "降水概率 75%、雷阵雨、气温 27℃",
                                        "reason": "午后有雷阵雨且气温偏高，改为室内活动",
                                    },
                                    {
                                        "time_range": "18:30-20:30",
                                        "title": "室内晚餐与影院",
                                        "activity_type": "indoor",
                                        "weather_basis": "降水概率 40%、气温 24℃",
                                        "reason": "傍晚降水减弱但仍有风险，选择室内活动",
                                    },
                                ],
                                "error_message": "",
                            },
                            ensure_ascii=False,
                        ),
                        tool_calls=[],
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        ),
    ]

    state = {"index": 0}

    async def _acompletion(**kwargs):
        index = min(state["index"], len(responses) - 1)
        state["index"] += 1
        return responses[index]

    litellm.acompletion = _acompletion


def fake_weather_tool(city: str, date_str: str | None = None) -> dict:
    """离线假天气数据（明天：小雨转雷阵雨）。"""
    return {
        "city": city,
        "date": date_str or TOMORROW,
        "weather_code": 95,
        "weather_text": "雷阵雨",
        "temperature_max": 27.0,
        "temperature_min": 19.0,
        "precipitation_probability_max": 0.75,
        "hourly": [
            {"time": "{0}T09:00".format(date_str or TOMORROW), "temperature": 21.0,
             "precipitation_probability": 0.1, "weather_code": 2, "weather_text": "多云"},
            {"time": "{0}T14:00".format(date_str or TOMORROW), "temperature": 27.0,
             "precipitation_probability": 0.75, "weather_code": 95, "weather_text": "雷阵雨"},
            {"time": "{0}T19:00".format(date_str or TOMORROW), "temperature": 24.0,
             "precipitation_probability": 0.4, "weather_code": 61, "weather_text": "小雨"},
        ],
    }


# --------------------------------------------------------------- 入口
async def main() -> None:
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    base_url = os.getenv("LLM_BASE_URL")

    if api_key:
        from weather_tools import build_weather_tools

        tools: list[ToolDef] = build_weather_tools()
        model_kwargs = {"base_url": base_url} if base_url else None
    else:
        print("[demo] 未检测到 LLM_API_KEY，使用内置假模型 + 假天气数据离线演示")
        install_fake_llm(None)
        tools = [
            {
                "name": "query_weather_forecast",
                "description": "查询指定城市在指定日期的天气",
                "func": fake_weather_tool,
            }
        ]
        model_kwargs = None

    plugin = WeatherPlanPlugin(
        model=model, temperature=1, api_key=api_key, model_kwargs=model_kwargs
    )
    result = await plugin.weather_plan_agent(
        {"city": "杭州", "activity_types": ["outdoor", "indoor"], "people_count": 2},
        tools,
    )
    print(
        json.dumps(
            {
                "data": result.model_dump(),
                "ok": result.ok,
                "finish_reason": result.finish_reason,
                "meta": result.meta,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
