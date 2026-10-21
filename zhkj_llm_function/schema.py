"""Pydantic v1/v2 兼容层与 JSON Schema 反射工具。

框架不手写工具 parameters：工具函数的签名由 `inspect` + `typing.get_type_hints`
反射生成 JSON Schema，agent 的入参/出参由 Pydantic 模型反射生成。
"""

import inspect
import json
import typing
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

_PYDANTIC_V2 = hasattr(BaseModel, "model_validate")


def model_validate(model_cls: type, data: Any) -> BaseModel:
    """按当前 pydantic 版本校验数据。"""
    if _PYDANTIC_V2:
        return model_cls.model_validate(data)
    return model_cls.parse_obj(data)  # pragma: no cover - pydantic v1 分支


def model_dump(model: BaseModel) -> Dict[str, Any]:
    """按当前 pydantic 版本导出字典。"""
    if _PYDANTIC_V2:
        return model.model_dump()
    return model.dict()  # pragma: no cover - pydantic v1 分支


def model_json(model: BaseModel) -> str:
    """序列化为 JSON 字符串。"""
    if _PYDANTIC_V2:
        return model.model_dump_json()
    return model.json()  # pragma: no cover - pydantic v1 分支


def model_schema(model_cls: type) -> Dict[str, Any]:
    """返回模型的 JSON Schema。"""
    if _PYDANTIC_V2:
        return model_cls.model_json_schema()
    return model_cls.schema()  # pragma: no cover - pydantic v1 分支


_JSON_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    tuple: "array",
    set: "array",
    dict: "object",
    type(None): "null",
}


def _type_to_schema(annotation: Any) -> Dict[str, Any]:
    """把 Python 类型注解转换成 JSON Schema 片段。"""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        if len(non_none) == 1:
            return _type_to_schema(non_none[0])
        return {"anyOf": [_type_to_schema(a) for a in non_none]}

    if origin in (list, set, tuple):
        item = _type_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}

    if origin is dict:
        return {"type": "object"}

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return model_schema(annotation)

    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}

    return {"type": "string"}


def tool_parameters_schema(func: Callable[..., Any]) -> Dict[str, Any]:
    """从工具函数签名反射生成 JSON Schema（框架自动完成，无需手写）。"""
    signature = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # pragma: no cover - 注解无法解析时退化为空类型
        hints = {}

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for name, param in signature.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        annotation = hints.get(name, param.annotation)
        properties[name] = _type_to_schema(annotation)
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def describe_docstring(func: Callable[..., Any]) -> str:
    """取函数的业务指令 docstring。"""
    doc = inspect.getdoc(func)
    return (doc or "").strip()


def json_dumps(data: Any) -> str:
    """稳定的 JSON 序列化。"""
    return json.dumps(data, ensure_ascii=False, default=str)


def resolve_model_hints(func: Callable[..., Any]) -> Dict[str, Any]:
    """解析函数注解，失败时返回空字典。"""
    try:
        return typing.get_type_hints(func, include_extras=True)
    except TypeError:  # pragma: no cover - Python 3.8 无 include_extras
        try:
            return typing.get_type_hints(func)
        except Exception:
            return {}
    except Exception:  # pragma: no cover
        return {}


def optional_str(value: Optional[str]) -> str:
    """None 安全地转成字符串。"""
    return "" if value is None else value
