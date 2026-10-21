"""框架端到端测试（pytest，tinyagent 引擎 + 伪造 litellm 响应）。"""

import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from zhkj_llm_function import (  # noqa: E402
    AgentNotRegisteredError,
    BaseAgentGroup,
    InputSchemaError,
    OutputSchemaError,
    ToolDef,
    ToolExecutionError,
    agent_def,
    parse_tool_call,
    render_tool_call,
)


# --------------------------------------------------------------------- 伪模型
class FakeLLM(object):
    """按顺序返回预设的 OpenAI 风格响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def make_response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None)


def make_tool_call(name, arguments, call_id="call_1"):
    function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    return SimpleNamespace(id=call_id, function=function, type="function")


@pytest.fixture(autouse=True)
def fake_litellm(monkeypatch):
    holder = {}

    def install(responses):
        fake = FakeLLM(responses)
        holder["fake"] = fake

        async def _acompletion(**kwargs):
            return await fake(**kwargs)

        monkeypatch.setattr(litellm, "acompletion", _acompletion)
        return fake

    holder["install"] = install
    return holder


# --------------------------------------------------------------------- 插件样例
class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    text: str
    length: int


class DemoPlugin(BaseAgentGroup):
    @agent_def(ttl=86400, compact_config=None)
    async def echo_agent(self, payload: EchoInput, tools=None) -> EchoOutput:
        """
        角色：回声助手
        业务规则：
        1. 使用工具查询后再输出。
        """
        ...


class ShortLivedPlugin(BaseAgentGroup):
    @agent_def(ttl=1, compact_config=None)
    async def echo_agent(self, payload: EchoInput, tools=None) -> EchoOutput:
        """
        角色：回声助手
        业务规则：
        1. 使用工具查询后再输出。
        """
        ...


def make_tools(calls):
    async def query_echo(text: str) -> str:
        calls.append(text)
        return "ECHO:" + text

    return [{"name": "query_echo", "description": "回声查询", "func": query_echo}]


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------- 用例
def test_agent_registration_and_schema():
    assert DemoPlugin.agent_names() == ["echo_agent"]
    spec = DemoPlugin.get_agent_spec("echo_agent")
    assert spec.ttl == 86400
    assert spec.input_schema["properties"]["text"]["type"] == "string"
    assert "text" in spec.output_schema["properties"]
    assert "回声助手" in spec.business_instruction


def test_tool_schema_is_reflected_not_written():
    from zhkj_llm_function.tools import normalize_tools

    specs = normalize_tools(make_tools([]))
    assert specs[0].parameters["properties"]["text"]["type"] == "string"
    assert specs[0].parameters["required"] == ["text"]


def test_native_tool_call_roundtrip(fake_litellm):
    calls = []
    fake = fake_litellm["install"](
        [
            make_response(
                content="",
                tool_calls=[make_tool_call("query_echo", {"text": "hi"})],
            ),
            make_response(content=json.dumps({"text": "ECHO:hi", "length": 7})),
        ]
    )
    plugin = DemoPlugin(model="gpt-4o-mini")
    result = run(plugin.run("echo_agent", {"text": "hi"}, tools=make_tools(calls)))

    # 返回值就是出参模型实例，外层可按签名直接接收
    assert isinstance(result, EchoOutput)
    assert result.text == "ECHO:hi"
    assert result.length == 7
    # 扩展信息通过代理属性提供，不污染出参结构
    assert result.ok is True
    assert result.finish_reason == "stop"
    assert result.meta["tool_calls"] == 1
    assert set(result.model_dump().keys()) == {"text", "length"}
    assert calls == ["hi"]
    # 工具 schema 由框架自动反射给模型
    tool_names = [item["function"]["name"] for item in fake.calls[0]["tools"]]
    assert "query_echo" in tool_names
    echo_schema = [
        item["function"]["parameters"]
        for item in fake.calls[0]["tools"]
        if item["function"]["name"] == "query_echo"
    ][0]
    assert echo_schema["properties"]["text"]


def test_marker_protocol_tool_call(fake_litellm):
    calls = []
    fake_litellm["install"](
        [
            make_response(content=render_tool_call("query_echo", {"text": "hi"})),
            make_response(content=json.dumps({"text": "ECHO:hi", "length": 7})),
        ]
    )
    plugin = DemoPlugin(model="gpt-4o-mini")
    result = run(plugin.run("echo_agent", {"text": "hi"}, tools=make_tools(calls)))

    assert isinstance(result, EchoOutput)
    assert result.text == "ECHO:hi"
    assert calls == ["hi"]


def test_direct_method_call_delegates_to_runtime(fake_litellm):
    fake_litellm["install"]([make_response(content=json.dumps({"text": "x", "length": 1}))])
    plugin = DemoPlugin(model="gpt-4o-mini")
    # 与普通方法一样调用：位置参数 payload / tools
    result = run(plugin.echo_agent({"text": "x"}))
    assert isinstance(result, EchoOutput)
    assert result.length == 1

    result2 = run(plugin.echo_agent({"text": "x"}, make_tools([])))
    assert isinstance(result2, EchoOutput)

    result3 = run(plugin.echo_agent(payload={"text": "x"}, tools=None))
    assert isinstance(result3, EchoOutput)


def test_direct_method_call_with_session(fake_litellm):
    fake_litellm["install"]([make_response(content=json.dumps({"text": "x", "length": 1}))])
    plugin = DemoPlugin(model="gpt-4o-mini")
    result = run(plugin.echo_agent({"text": "x"}, session_id="s-direct"))
    assert isinstance(result, EchoOutput)
    assert result.meta["session_id"] == "s-direct"


def test_input_schema_invalid(fake_litellm):
    fake_litellm["install"]([make_response(content="{}")])
    plugin = DemoPlugin(model="gpt-4o-mini")
    with pytest.raises(InputSchemaError):
        run(plugin.run("echo_agent", {"wrong": 1}))


def test_output_schema_invalid(fake_litellm):
    fake_litellm["install"]([make_response(content="not json at all")])
    plugin = DemoPlugin(model="gpt-4o-mini")
    with pytest.raises(OutputSchemaError):
        run(plugin.run("echo_agent", {"text": "x"}))


def test_not_registered(fake_litellm):
    fake_litellm["install"]([make_response(content="{}")])
    plugin = DemoPlugin(model="gpt-4o-mini")
    with pytest.raises(AgentNotRegisteredError):
        run(plugin.run("missing_agent", {"text": "x"}))


def test_marker_protocol_unknown_tool(fake_litellm):
    fake_litellm["install"]([make_response(content=render_tool_call("missing_tool", {}))])
    plugin = DemoPlugin(model="gpt-4o-mini")
    with pytest.raises(ToolExecutionError):
        run(plugin.run("echo_agent", {"text": "x"}, tools=make_tools([])))


def test_run_envelope_keeps_raw_structure(fake_litellm):
    fake_litellm["install"]([make_response(content=json.dumps({"text": "ok", "length": 2}))])
    plugin = DemoPlugin(model="gpt-4o-mini")
    envelope = run(plugin.run_envelope("echo_agent", {"text": "x"}))
    assert envelope["ok"] is True
    assert envelope["finish_reason"] == "stop"
    assert isinstance(envelope["data"], EchoOutput)
    assert envelope["meta"]["token_original"] > 0


def test_session_reuse_and_reset(fake_litellm):
    fake_litellm["install"]([make_response(content=json.dumps({"text": "ok", "length": 2}))])
    plugin = ShortLivedPlugin(model="gpt-4o-mini")

    first = run(plugin.run("echo_agent", {"text": "x"}, session_id="s1"))
    assert first.finish_reason == "stop"
    assert first.meta["session_reset"] is False

    second = run(plugin.run("echo_agent", {"text": "x"}, session_id="s1"))
    assert second.meta["session_reset"] is False

    time.sleep(1.2)
    third = run(plugin.run("echo_agent", {"text": "x"}, session_id="s1"))
    assert third.finish_reason == "reset"
    assert third.meta["session_reset"] is True


def test_token_accounting(fake_litellm):
    fake_litellm["install"]([make_response(content=json.dumps({"text": "ok", "length": 2}))])
    plugin = DemoPlugin(model="gpt-4o-mini")
    result = run(plugin.run("echo_agent", {"text": "x"}))
    assert result.meta["token_original"] > 0
    assert result.meta["token_compacted"] > 0


def test_session_is_in_memory_only(fake_litellm):
    """会话仅存在于内存中：换个插件实例（等价于重启）就看不到旧会话。"""
    fake_litellm["install"]([make_response(content=json.dumps({"text": "ok", "length": 2}))])

    plugin = DemoPlugin(model="gpt-4o-mini")
    run(plugin.run("echo_agent", {"text": "x"}, session_id="s1"))
    assert plugin.session_ids() == ["s1"]

    # 新的插件实例（等价于进程重启）看不到旧会话
    other = DemoPlugin(model="gpt-4o-mini")
    assert other.session_ids() == []

    # 框架不再提供持久化入口
    with pytest.raises(TypeError):
        DemoPlugin(model="gpt-4o-mini", session_persistence_path="./x.db")


def test_protocol_parse():
    call = parse_tool_call(render_tool_call("a", {"x": 1}))
    assert call is not None
    assert call.name == "a"
    assert call.arguments == {"x": 1}
    assert parse_tool_call("hello") is None


def test_courier_plugin_shape():
    from plugins.courier_plugin import CourierAgentPlugin, CourierTraceInput

    specs = CourierAgentPlugin.agent_specs()
    assert "courier_trace_agent" in specs
    spec = specs["courier_trace_agent"]
    assert spec.ttl == 86400
    assert "业务规则" in spec.business_instruction
    assert set(spec.input_schema["properties"]) == {
        "waybill_no",
        "phone_tail",
        "include_history",
    }
    assert CourierTraceInput(waybill_no="SF1").include_history is True


def test_weather_plan_plugin_shape():
    from plugins.weather_plan_plugin import WeatherPlanInput, WeatherPlanPlugin

    specs = WeatherPlanPlugin.agent_specs()
    assert "weather_plan_agent" in specs
    spec = specs["weather_plan_agent"]
    assert "明天" in spec.business_instruction
    assert "query_weather_forecast" in spec.business_instruction
    assert set(spec.input_schema["properties"]) == {
        "city",
        "date",
        "activity_types",
        "people_count",
        "avoid_crowded",
    }
    assert set(spec.output_schema["properties"]) == {
        "plan_date",
        "city",
        "weather_summary",
        "temperature_range",
        "precipitation_probability_max",
        "has_heavy_rain",
        "suggestions",
        "plan",
        "error_message",
    }
    assert WeatherPlanInput(city="杭州").date is None


def test_weather_plan_agent_end_to_end(fake_litellm):
    """天气工具调用 -> 结构化行程输出。"""
    from datetime import date, timedelta

    from plugins.weather_plan_plugin import WeatherPlanOutput, WeatherPlanPlugin

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    fake = fake_litellm["install"](
        [
            make_response(
                content="",
                tool_calls=[
                    make_tool_call("query_weather_forecast", {"city": "杭州", "date_str": tomorrow})
                ],
            ),
            make_response(
                content=json.dumps(
                    {
                        "plan_date": tomorrow,
                        "city": "杭州",
                        "weather_summary": "多云转雷阵雨",
                        "temperature_range": "19~27℃",
                        "precipitation_probability_max": 0.75,
                        "has_heavy_rain": True,
                        "suggestions": ["午后有雷阵雨，带伞"],
                        "plan": [
                            {
                                "time_range": "08:30-11:30",
                                "title": "公园散步",
                                "activity_type": "outdoor",
                                "weather_basis": "降水概率 10%、气温 21℃",
                                "reason": "上午多云凉爽",
                            }
                        ],
                        "error_message": "",
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )

    async def query_weather_forecast(city: str, date_str: str | None = None) -> dict:
        return {"city": city, "date": date_str, "weather_text": "雷阵雨"}

    tools = [
        {
            "name": "query_weather_forecast",
            "description": "查询天气",
            "func": query_weather_forecast,
        }
    ]

    plugin = WeatherPlanPlugin(model="gpt-4o-mini")
    result = run(plugin.weather_plan_agent({"city": "杭州"}, tools))

    assert isinstance(result, WeatherPlanOutput)
    assert result.plan_date == tomorrow
    assert result.has_heavy_rain is True
    assert result.plan[0].activity_type == "outdoor"
    assert result.ok is True
    # 工具参数 schema 自动反射
    tool_schema = [
        item["function"]
        for item in fake.calls[0]["tools"]
        if item["function"]["name"] == "query_weather_forecast"
    ][0]
    assert set(tool_schema["parameters"]["properties"]) == {"city", "date_str"}
    # 用户输入里带有当前日期，便于模型换算「明天」
    assert "当前日期时间" in fake.calls[0]["messages"][-1]["content"]
