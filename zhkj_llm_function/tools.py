"""工具定义与执行。

工具在调用 agent 时由外部传入（`tools: list[ToolDef] | None`），
插件内部不允许定义工具函数。
工具的 parameters（JSON Schema）由 tinyagent 依据函数签名自动反射生成，
调用方与插件都不需要手写。
"""

import inspect
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

from tinyagent import tool as tinyagent_tool
from tinyagent.tiny_agent import _generate_schema_from_function

from .errors import ToolExecutionError, ToolNotFoundError


class ToolDef(TypedDict):
    """外部传入的工具描述。"""

    name: str
    description: str
    func: Callable[..., Awaitable[Any]]


class ToolSpec(object):
    """框架内部归一化后的工具。"""

    __slots__ = ("name", "description", "func", "parameters", "handler")

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., Any],
        parameters: Dict[str, Any],
        handler: Any = None,
    ) -> None:
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters
        self.handler = handler

    def describe(self) -> str:
        """渲染给大模型的工具说明。"""
        return "- {name}: {desc}".format(name=self.name, desc=self.description)


def _schema_for(func: Callable[..., Any]) -> Dict[str, Any]:
    """自动从函数签名反射出参数 JSON Schema。"""
    try:
        return _generate_schema_from_function(func)
    except Exception:  # pragma: no cover - 反射失败时退化为空对象
        return {"type": "object", "properties": {}, "required": []}


def normalize_tools(tools: Optional[List[ToolDef]]) -> List[ToolSpec]:
    """把外部传入的 ToolDef 列表归一化为 tinyagent 可注册的工具。"""
    specs: List[ToolSpec] = []
    for item in tools or []:
        if not isinstance(item, dict) or "name" not in item or "func" not in item:
            raise ToolExecutionError("工具定义必须包含 name 与 func 字段")
        func = item["func"]
        if not callable(func):
            raise ToolExecutionError("工具 {0} 的 func 不可调用".format(item["name"]))

        name = str(item["name"])
        description = str(item.get("description", "") or (inspect.getdoc(func) or ""))
        parameters = _schema_for(func)

        # 用 tinyagent 的 tool 装饰器包装：自动生成 parameters，无需手写
        handler = tinyagent_tool(name=name, description=description, schema=parameters)(func)
        specs.append(
            ToolSpec(
                name=name,
                description=description,
                func=func,
                parameters=parameters,
                handler=handler,
            )
        )
    return specs


def find_tool(specs: List[ToolSpec], name: str) -> ToolSpec:
    """按名称查找工具。"""
    for spec in specs:
        if spec.name == name:
            return spec
    raise ToolNotFoundError("未找到工具: {0}".format(name))


def register_tools(agent: Any, specs: List[ToolSpec]) -> None:
    """把工具注册到 tinyagent 实例（同名工具会被替换）。"""
    for spec in specs:
        agent.custom_tools[:] = [
            item
            for item in getattr(agent, "custom_tools", [])
            if item.get("function", {}).get("name") != spec.name
        ]
        agent.available_tools[:] = [
            item
            for item in getattr(agent, "available_tools", [])
            if item.get("function", {}).get("name") != spec.name
        ]
        # add_tool 会把 schema 写入 custom_tools / available_tools 并登记 handler
        agent.add_tool(spec.handler)


async def execute_tool(spec: ToolSpec, arguments: Dict[str, Any]) -> str:
    """执行工具并把结果转换为字符串。"""
    try:
        result = spec.func(**arguments)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001 - 工具异常统一包装
        raise ToolExecutionError(
            "工具 {0} 执行失败: {1}: {2}".format(spec.name, type(exc).__name__, exc)
        )

    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list, tuple)):
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception:  # pragma: no cover
            return str(result)
    return str(result)
