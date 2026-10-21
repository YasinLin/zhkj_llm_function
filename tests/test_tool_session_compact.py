"""工具调用 + 会话历史 + 上下文压缩 的组合测试。

覆盖：
- 同一 session_id 内，工具调用与工具结果进入后续轮次的模型上下文
- 会话上下文保留完整的 system/user/assistant/tool 序列
- 超过阈值触发自动压缩，压缩后上下文只保留 system + 摘要
- 压缩后仍可继续发起工具调用
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from zhkj_llm_function import BaseAgentGroup, agent_def  # noqa: E402


# --------------------------------------------------------------------- 伪模型
class FakeLLM(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def make_response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


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


def run(coro):
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------------- 插件
class LookupInput(BaseModel):
    text: str


class LookupOutput(BaseModel):
    text: str
    length: int


class LookupPlugin(BaseAgentGroup):
    @agent_def(ttl=86400, compact_config=None)
    async def lookup_agent(self, payload: LookupInput, tools=None) -> LookupOutput:
        """
        角色：查询助手
        业务规则：
        1. 先调用 query_lookup 工具，再输出 JSON。
        """
        ...


def make_tools(calls):
    async def query_lookup(text: str) -> str:
        calls.append(text)
        return "LOOKUP:" + text

    return [{"name": "query_lookup", "description": "查询工具", "func": query_lookup}]


def roles_of(call):
    return [message.get("role") for message in call["messages"]]


# --------------------------------------------------------------------- 用例
def test_tool_result_enters_next_turn_context(fake_litellm):
    """同一会话第二轮的上下文里带上了第一轮的工具结果。"""
    calls = []
    fake = fake_litellm["install"](
        [
            make_response(content="", tool_calls=[make_tool_call("query_lookup", {"text": "hi"})]),
            make_response(content=json.dumps({"text": "LOOKUP:hi", "length": 9})),
            make_response(content=json.dumps({"text": "LOOKUP:hi again", "length": 17})),
        ]
    )

    plugin = LookupPlugin(model="gpt-4o-mini")
    first = run(plugin.run("lookup_agent", {"text": "hi"}, tools=make_tools(calls), session_id="s1"))
    assert first.meta["tool_calls"] == 1
    assert calls == ["hi"]

    second = run(plugin.run("lookup_agent", {"text": "again"}, tools=make_tools(calls), session_id="s1"))
    assert second.meta["session_reset"] is False

    # 第一轮：system + user + assistant(tool_call) + tool
    assert roles_of(fake.calls[0]) == ["system", "user"]
    # 第二轮：历史里的 tool 消息仍在上下文中
    assert "tool" in roles_of(fake.calls[1])
    tool_messages = [m for m in fake.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages[0]["content"] == "LOOKUP:hi"
    assert "assistant" in roles_of(fake.calls[1])


def test_tool_result_stays_in_session_context(fake_litellm):
    """会话内保留完整上下文：工具调用、工具结果、模型回答都在。"""
    calls = []
    db_path = None
    fake = fake_litellm["install"](
        [
            make_response(content="", tool_calls=[make_tool_call("query_lookup", {"text": "hi"})]),
            make_response(content=json.dumps({"text": "LOOKUP:hi", "length": 9})),
            make_response(content=json.dumps({"text": "LOOKUP:hi again", "length": 17})),
        ]
    )

    plugin = LookupPlugin(model="gpt-4o-mini")
    run(plugin.run("lookup_agent", {"text": "hi"}, tools=make_tools(calls), session_id="s1"))
    assert calls == ["hi"]

    session = plugin.session_store.peek("s1")
    roles = [message.get("role") for message in session.agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]


def test_auto_compaction_triggers_and_shrinks_context(fake_litellm):
    """超过 max_prompt_tokens 时自动压缩，压缩后上下文只保留 system + 摘要。"""
    fake = fake_litellm["install"](
        [
            make_response(content="", tool_calls=[make_tool_call("query_lookup", {"text": "hi"})]),
            make_response(content=json.dumps({"text": "LOOKUP:hi", "length": 9})),
            make_response(content="会话摘要：之前查询了 LOOKUP:hi。"),
        ]
    )

    plugin = LookupPlugin(
        model="gpt-4o-mini",
        compact_config={"enabled": True, "max_prompt_tokens": 1},
    )
    result = run(
        plugin.run("lookup_agent", {"text": "hi"}, tools=make_tools([]), session_id="s-compact")
    )

    assert result.meta["compactions"] == 1
    assert result.meta["token_original"] > 0
    assert result.meta["token_compacted"] <= result.meta["token_original"]

    session = plugin.session_store.peek("s-compact")
    roles = [message.get("role") for message in session.agent.messages]
    assert roles == ["system", "user"]
    assert "会话摘要" in session.agent.messages[1]["content"]


def test_compaction_keeps_session_usable(fake_litellm):
    """压缩只影响模型上下文，会话本身仍可继续使用。"""
    db_path = None
    fake = fake_litellm["install"](
        [
            make_response(content="", tool_calls=[make_tool_call("query_lookup", {"text": "hi"})]),
            make_response(content=json.dumps({"text": "LOOKUP:hi", "length": 9})),
            make_response(content="会话摘要：之前查询了 LOOKUP:hi。"),
        ]
    )

    plugin = LookupPlugin(
        model="gpt-4o-mini",
        compact_config={"enabled": True, "max_prompt_tokens": 1},
    )
    run(plugin.run("lookup_agent", {"text": "hi"}, tools=make_tools([]), session_id="s1"))

    # 压缩后上下文只剩 system + 摘要
    session = plugin.session_store.peek("s1")
    assert len(session.agent.messages) == 2
    assert "会话摘要" in session.agent.messages[1]["content"]


def test_tool_call_still_works_after_compaction(fake_litellm):
    """压缩之后，同一会话仍能继续发起并执行工具调用。"""
    calls = []
    fake = fake_litellm["install"](
        [
            make_response(content="", tool_calls=[make_tool_call("query_lookup", {"text": "one"})]),
            make_response(content=json.dumps({"text": "LOOKUP:one", "length": 10})),
            make_response(content="会话摘要：之前查询了 LOOKUP:one。"),
            make_response(content="", tool_calls=[make_tool_call("query_lookup", {"text": "two"})]),
            make_response(content=json.dumps({"text": "LOOKUP:two", "length": 10})),
        ]
    )

    plugin = LookupPlugin(
        model="gpt-4o-mini",
        compact_config={"enabled": True, "max_prompt_tokens": 1},
    )
    run(plugin.run("lookup_agent", {"text": "one"}, tools=make_tools(calls), session_id="s1"))
    second = run(
        plugin.run("lookup_agent", {"text": "two"}, tools=make_tools(calls), session_id="s1")
    )

    assert calls == ["one", "two"]
    assert second.meta["tool_calls"] == 1
    assert second.finish_reason == "stop"
