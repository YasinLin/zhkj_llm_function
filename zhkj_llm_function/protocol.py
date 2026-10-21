"""工具调用协议解析。

框架统一使用 <|tool_call|> 标记协议：

    <|tool_call|>
    {"name": "工具名称", "arguments": {}}
    <|end_tool_call|>

一轮只支持调用单个工具。
"""

import json
import re
from typing import Any, Dict, Optional

TOOL_CALL_START = "<|tool_call|>"
TOOL_CALL_END = "<|end_tool_call|>"

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class ToolCall(object):
    """一次工具调用请求。"""

    __slots__ = ("name", "arguments", "raw")

    def __init__(self, name: str, arguments: Dict[str, Any], raw: str) -> None:
        self.name = name
        self.arguments = arguments
        self.raw = raw

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "ToolCall(name={0!r}, arguments={1!r})".format(self.name, self.arguments)


def strip_code_fence(text: str) -> str:
    """去掉 markdown 代码围栏。"""
    match = _FENCE_RE.search(text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip()


def extract_json_payload(text: str) -> Optional[Any]:
    """从模型输出里抽取 JSON 对象/数组。"""
    if text is None:
        return None
    candidate = strip_code_fence(text)
    if not candidate:
        return None

    try:
        return json.loads(candidate)
    except Exception:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(candidate):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(candidate[index:])
        except Exception:
            continue
        return value
    return None


def parse_tool_call(text: str) -> Optional[ToolCall]:
    """解析文本中的工具调用标记；没有则返回 None。"""
    if not text or TOOL_CALL_START not in text:
        return None

    segment = text.split(TOOL_CALL_START, 1)[1]
    if TOOL_CALL_END in segment:
        segment = segment.split(TOOL_CALL_END, 1)[0]
    segment = strip_code_fence(segment.strip())
    if not segment:
        return None

    payload = extract_json_payload(segment)
    if not isinstance(payload, dict):
        return None

    name = payload.get("name")
    if not isinstance(name, str) or not name:
        return None

    arguments = payload.get("arguments", {})
    if arguments is None:
        arguments = {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}

    return ToolCall(name=name, arguments=arguments, raw=segment)


def render_tool_call(name: str, arguments: Dict[str, Any]) -> str:
    """按框架协议渲染工具调用文本（供 mock / 测试使用）。"""
    return "{0}\n{1}\n{2}".format(
        TOOL_CALL_START,
        json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False),
        TOOL_CALL_END,
    )
