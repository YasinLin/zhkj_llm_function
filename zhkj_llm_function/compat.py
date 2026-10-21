"""与 tinyagent / litellm 消息对象的兼容转换。

问题背景：
- tinyagent 的 `_format_conversation_for_summary()` 用 `tool_call.get("function", {})` 读取工具调用，
  但运行期 assistant 消息里的 `tool_calls` 是 litellm/OpenAI 对象（属性访问），压缩时会抛
  `AttributeError: 'X' object has no attribute 'get'`。

因此统一提供：对象 -> dict 的转换。
"""

from typing import Any, Dict, Iterable, List


def _get(source: Any, key: str, default: Any = None) -> Any:
    """同时兼容 dict 与对象属性两种取值方式。"""
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def tool_call_to_dict(call: Any) -> Dict[str, Any]:
    """把一个工具调用（dict 或对象）转成可序列化的 dict。"""
    if isinstance(call, dict):
        function = call.get("function") or {}
        return {
            "id": call.get("id", ""),
            "type": call.get("type", "function"),
            "function": {
                "name": _get(function, "name", ""),
                "arguments": _get(function, "arguments", "{}"),
            },
        }

    function = _get(call, "function")
    return {
        "id": _get(call, "id", "") or "",
        "type": _get(call, "type", "function") or "function",
        "function": {
            "name": _get(function, "name", "") or "",
            "arguments": _get(function, "arguments", "{}") or "{}",
        },
    }


def message_to_plain_dict(message: Any) -> Dict[str, Any]:
    """把一条消息转成纯 dict（tool_calls 一并转换）。"""
    if not isinstance(message, dict):
        message = {
            "role": _get(message, "role", "unknown"),
            "content": _get(message, "content", ""),
        }
    plain = dict(message)
    calls = plain.get("tool_calls")
    if isinstance(calls, Iterable) and not isinstance(calls, (str, bytes, dict)):
        plain["tool_calls"] = [tool_call_to_dict(call) for call in calls]
    return plain


def messages_for_summary(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """生成用于摘要的副本：tool_calls 统一为 dict，避免引擎侧属性访问报错。"""
    return [message_to_plain_dict(message) for message in messages or []]
