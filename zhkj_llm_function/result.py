"""结果对象：按 agent 声明的出参签名返回，扩展信息通过代理属性暴露。

- 方法返回值就是声明的出参模型实例（`isinstance(result, XXXOutput) == True`），
  外层可以直接按出参签名接收，`model_dump()` 只包含声明字段。
- 框架扩展信息（ok / finish_reason / meta）通过动态子类的代理属性提供，
  不污染出参结构，也不会进入 `model_dump()`。
"""

from typing import Any, Dict, Optional

from pydantic import PrivateAttr

from .errors import (
    AgentNotRegisteredError,
    FrameworkError,
    InputSchemaError,
    OutputSchemaError,
    ToolExecutionError,
)

_FINISH_ERROR_MAP = {
    "not_registered": AgentNotRegisteredError,
    "input_schema_invalid": InputSchemaError,
    "output_schema_invalid": OutputSchemaError,
    "tool_error": ToolExecutionError,
}


def build_result(model: Any, meta: Optional[Dict[str, Any]] = None, finish_reason: str = "stop"):
    """把出参模型包装成带代理属性的结果对象。

    返回对象仍然是出参模型（动态子类）的实例，字段与序列化结果完全不变。
    """
    meta = meta or {}
    base = type(model)
    meta_cls = type(base)

    namespace = {
        "_framework_meta": PrivateAttr(default=None),
        "_framework_finish_reason": PrivateAttr(default=""),
        "_framework_ok": PrivateAttr(default=True),
        "ok": property(lambda self: self._framework_ok),
        "finish_reason": property(lambda self: self._framework_finish_reason),
        "meta": property(lambda self: self._framework_meta),
        "error": property(lambda self: (self._framework_meta or {}).get("error", "")),
    }
    result_cls = meta_cls(
        "{0}Result".format(base.__name__),
        (base,),
        namespace,
    )

    obj = model
    try:
        obj.__class__ = result_cls
        object.__setattr__(
            obj,
            "__pydantic_private__",
            {
                "_framework_meta": meta,
                "_framework_finish_reason": finish_reason,
                "_framework_ok": True,
            },
        )
    except Exception:  # pragma: no cover - 极端情况下退化为原模型
        return model
    return obj


def envelope_to_result(envelope: Dict[str, Any]):
    """把运行时 envelope 转成出参对象；失败时抛出对应异常。"""
    if envelope.get("ok") and envelope.get("data") is not None:
        return build_result(
            envelope["data"],
            envelope.get("meta") or {},
            envelope.get("finish_reason", "stop"),
        )

    finish_reason = envelope.get("finish_reason", "error")
    message = envelope.get("error") or "agent 执行失败: {0}".format(finish_reason)
    error_cls = _FINISH_ERROR_MAP.get(finish_reason, FrameworkError)
    raise error_cls(message)
