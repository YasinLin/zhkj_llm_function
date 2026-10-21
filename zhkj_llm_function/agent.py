"""Agent 声明层：`agent_def` 装饰器与 `BaseAgentGroup`。

框架核心模式：基于 `@agent_def` 装饰器 + Pydantic 类型注解反射；
执行引擎使用 tinyagent-py。
"""

import functools
import inspect
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from .errors import AgentNotRegisteredError, FrameworkError
from .schema import describe_docstring, model_schema, resolve_model_hints

DEFAULT_TTL = 86400


class AgentSpec(object):
    """一个 agent 的声明信息（由 @agent_def 生成）。"""

    __slots__ = (
        "name",
        "ttl",
        "compact_config",
        "input_model",
        "output_model",
        "input_schema",
        "output_schema",
        "business_instruction",
        "func",
        "group_cls",
    )

    def __init__(
        self,
        name: str,
        ttl: int,
        compact_config: Optional[Dict[str, Any]],
        input_model: type,
        output_model: type,
        business_instruction: str,
        func: Callable[..., Any],
        group_cls: Optional[type] = None,
    ) -> None:
        self.name = name
        self.ttl = ttl
        self.compact_config = compact_config
        self.input_model = input_model
        self.output_model = output_model
        self.input_schema = model_schema(input_model)
        self.output_schema = model_schema(output_model)
        self.business_instruction = business_instruction
        self.func = func
        self.group_cls = group_cls

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "AgentSpec(name={0!r}, input={1}, output={2})".format(
            self.name, self.input_model.__name__, self.output_model.__name__
        )


def _require_model(annotation: Any, kind: str, agent_name: str) -> type:
    """校验注解必须是 Pydantic BaseModel 子类。"""
    if annotation is None or not isinstance(annotation, type):
        raise FrameworkError(
            "agent [{0}] 的 {1} 必须使用 Pydantic BaseModel 进行类型注解".format(agent_name, kind)
        )
    if not issubclass(annotation, BaseModel):
        raise FrameworkError(
            "agent [{0}] 的 {1} 必须是 Pydantic BaseModel 子类，当前为 {2}".format(
                agent_name, kind, annotation.__name__
            )
        )
    return annotation


def agent_def(
    ttl: int = DEFAULT_TTL, compact_config: Optional[Dict[str, Any]] = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """标记一个 async 方法为 agent。

    参数：
    - ttl：会话有效期（秒），常用 86400（一天）。
    - compact_config：None=使用全局默认前缀压缩策略，也可传入自定义压缩配置 dict。

    方法签名固定为：
        async def xxx_agent(self, payload: XXXInput, tools: list[ToolDef] | None = None) -> XXXOutput
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(func):
            raise FrameworkError(
                "@agent_def 只能装饰 async 方法: {0}".format(getattr(func, "__name__", func))
            )
        if not isinstance(ttl, int) or ttl <= 0:
            raise FrameworkError("@agent_def 的 ttl 必须是正整数")
        if compact_config is not None and not isinstance(compact_config, dict):
            raise FrameworkError("@agent_def 的 compact_config 必须是 dict 或 None")

        signature = inspect.signature(func)
        parameters = list(signature.parameters.values())
        if len(parameters) < 2 or parameters[0].name != "self":
            raise FrameworkError("@agent_def 方法必须保留 self 作为第一个参数")
        if parameters[1].name != "payload":
            raise FrameworkError("@agent_def 方法的第二个参数必须命名为 payload")
        if len(parameters) < 3 or parameters[2].name != "tools":
            raise FrameworkError("@agent_def 方法的第三个参数必须命名为 tools")

        hints = resolve_model_hints(func)
        input_model = _require_model(hints.get("payload"), "入参(payload)", func.__name__)
        output_model = _require_model(hints.get("return"), "出参(return)", func.__name__)

        spec = AgentSpec(
            name=func.__name__,
            ttl=ttl,
            compact_config=compact_config,
            input_model=input_model,
            output_model=output_model,
            business_instruction=describe_docstring(func),
            func=func,
        )

        @functools.wraps(func)
        async def wrapper(
            self: Any,
            payload: Any = None,
            tools: Optional[List[Any]] = None,
            session_id: Optional[str] = None,
        ) -> Any:
            """以普通方法方式调用：await plugin.xxx_agent(payload, tools)。

            返回值即声明的出参模型实例（外层可按出参签名直接接收）；
            扩展信息（ok / finish_reason / meta）通过代理属性提供。
            执行失败时抛出对应异常（InputSchemaError / OutputSchemaError 等）。
            """
            runtime = getattr(self, "_runtime", None)
            if runtime is not None:
                from .result import envelope_to_result

                envelope = await runtime.run(
                    spec.name, payload, tools=tools, session_id=session_id
                )
                return envelope_to_result(envelope)
            # 未接入引擎时退化为声明式空实现（插件桩）
            return await func(self, payload, tools)

        wrapper.__agent_spec__ = spec  # type: ignore[attr-defined]
        wrapper.__signature__ = inspect.Signature(
            [
                inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                inspect.Parameter("payload", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None),
                inspect.Parameter("tools", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None),
                inspect.Parameter("session_id", inspect.Parameter.KEYWORD_ONLY, default=None),
            ]
        )
        wrapper.__annotations__ = dict(getattr(func, "__annotations__", {}))
        return wrapper

    return decorator


class BaseAgentGroup(object):
    """插件基类：所有 agent 能力通过 @agent_def 注册在本类中。

    执行引擎为 tinyagent-py；模型、鉴权、压缩策略等通过构造参数注入。
    """

    def __init__(
        self,
        model: str = "gpt-5",
        api_key: Optional[str] = None,
        model_kwargs: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        session_store: Any = None,
        compact_config: Optional[Dict[str, Any]] = None,
        global_compact_config: Optional[Dict[str, Any]] = None,
        max_tool_iterations: int = 8,
        max_turns: int = 10,
        enable_todo_write: bool = False,
        storage: Any = None,
        logger: Any = None,
    ) -> None:
        from .runtime import AgentRuntime

        effective_compact_config = (
            compact_config if compact_config is not None else global_compact_config
        )
        self._global_compact_config = effective_compact_config
        self._runtime = AgentRuntime(
            group=self,
            model=model,
            api_key=api_key,
            model_kwargs=model_kwargs,
            temperature=temperature,
            session_store=session_store,
            global_compact_config=effective_compact_config,
            max_tool_iterations=max_tool_iterations,
            max_turns=max_turns,
            enable_todo_write=enable_todo_write,
            storage=storage,
            logger=logger,
        )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        specs: Dict[str, AgentSpec] = {}
        for base in reversed(cls.__mro__):
            for attr_name, attr in vars(base).items():
                spec = getattr(attr, "__agent_spec__", None)
                if isinstance(spec, AgentSpec):
                    spec.group_cls = cls
                    specs[attr_name] = spec
        cls.__agent_specs__ = specs  # type: ignore[attr-defined]

    # ---------- 声明查询 ----------
    @classmethod
    def agent_specs(cls) -> Dict[str, AgentSpec]:
        """本插件注册的全部 agent 声明。"""
        return dict(getattr(cls, "__agent_specs__", {}))

    @classmethod
    def agent_names(cls) -> List[str]:
        """已注册的 agent 名称列表。"""
        return sorted(cls.agent_specs().keys())

    @classmethod
    def get_agent_spec(cls, agent_name: str) -> AgentSpec:
        """按名称取 agent 声明；不存在则抛异常。"""
        specs = cls.agent_specs()
        if agent_name not in specs:
            raise AgentNotRegisteredError(
                "未注册的 agent: {0}，可用: {1}".format(agent_name, ", ".join(sorted(specs)))
            )
        return specs[agent_name]

    # ---------- 执行 ----------
    @property
    def runtime(self) -> Any:
        """运行时对象。"""
        return self._runtime

    @property
    def session_store(self) -> Any:
        """会话存储（内存存储，进程内有效）。"""
        return self._runtime.session_store

    def session_ids(self) -> List[str]:
        """当前进程内已知的 session_id 列表。"""
        return self._runtime.session_store.list_session_ids()

    def set_llm(self, client: Any) -> None:
        """切换模型 / 鉴权信息。"""
        if isinstance(client, str):
            self._runtime.model = client
        elif isinstance(client, dict):
            self._runtime.model = client.get("model", self._runtime.model)
            self._runtime.api_key = client.get("api_key", self._runtime.api_key)
        else:
            raise FrameworkError("set_llm 仅支持模型名或配置 dict")

    async def run(
        self,
        agent_name: str,
        payload: Any,
        tools: Optional[List[Any]] = None,
        session_id: Optional[str] = None,
    ) -> Any:
        """执行指定 agent，返回声明的出参模型实例（扩展信息见代理属性）。"""
        from .result import envelope_to_result

        envelope = await self._runtime.run(
            agent_name, payload, tools=tools, session_id=session_id
        )
        return envelope_to_result(envelope)

    async def run_envelope(
        self,
        agent_name: str,
        payload: Any,
        tools: Optional[List[Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行指定 agent，返回框架原始封装结构 {ok, data, finish_reason, meta}。"""
        return await self._runtime.run(agent_name, payload, tools=tools, session_id=session_id)
