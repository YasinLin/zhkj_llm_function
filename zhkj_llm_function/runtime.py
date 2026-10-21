"""运行时：基于 tinyagent 执行 agent，产出框架统一返回结构。

引擎：tinyagent-py（ReAct 循环 + 工具调用 + 上下文压缩 + token 统计）。
框架层职责：
- @agent_def 声明的入参/出参校验
- 外部工具注册（参数 schema 由函数签名自动反射）
- 业务指令 docstring -> system prompt
- <|tool_call|> 标记协议兜底（tinyagent 原生工具调用之外的兼容路径）
- 会话 ttl 管控与统一返回结构封装

框架返回结构（插件不用管）：

    {
      "ok": bool,
      "data": 输出模型实例,
      "finish_reason": "stop|reset|compact|not_registered|input_schema_invalid|output_schema_invalid|tool_error|error",
      "meta": {
        "agent_name": str,
        "token_original": int,
        "token_compacted": int
      }
    }
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .agent import AgentSpec
from .compat import messages_for_summary
from .errors import (
    AgentNotRegisteredError,
    FrameworkError,
    InputSchemaError,
    OutputSchemaError,
    ToolExecutionError,
    ToolNotFoundError,
)
from .protocol import extract_json_payload, parse_tool_call
from .schema import json_dumps, model_dump, model_validate
from .session import SessionStore
from .tools import ToolSpec, execute_tool, find_tool, normalize_tools, register_tools

FINISH_STOP = "stop"
FINISH_RESET = "reset"
FINISH_COMPACT = "compact"
FINISH_NOT_REGISTERED = "not_registered"
FINISH_INPUT_INVALID = "input_schema_invalid"
FINISH_OUTPUT_INVALID = "output_schema_invalid"
FINISH_TOOL_ERROR = "tool_error"
FINISH_ERROR = "error"

DEFAULT_COMPACT_CONFIG: Dict[str, Any] = {
    "max_prompt_tokens": 8000,
    "enabled": True,
}


class AgentRuntime(object):
    """插件实例的运行时。"""

    def __init__(
        self,
        group: Any,
        model: str = "gpt-5",
        api_key: Optional[str] = None,
        model_kwargs: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        session_store: Optional[SessionStore] = None,
        global_compact_config: Optional[Dict[str, Any]] = None,
        max_tool_iterations: int = 8,
        max_turns: int = 10,
        enable_todo_write: bool = False,
        storage: Any = None,
        logger: Any = None,
    ) -> None:
        self.group = group
        self.model = model
        self.api_key = api_key
        self.model_kwargs = dict(model_kwargs or {})
        self.temperature = temperature
        self.session_store = session_store or SessionStore()
        self.global_compact_config = global_compact_config
        self.max_tool_iterations = max_tool_iterations
        self.max_turns = max_turns
        self.enable_todo_write = enable_todo_write
        self.storage = storage
        self.logger = logger

    # ------------------------------------------------------------------ 公共入口
    async def run(
        self,
        agent_name: str,
        payload: Any,
        tools: Optional[List[Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行一次 agent 调用，返回框架统一封装结构。"""
        meta = {
            "agent_name": agent_name,
            "token_original": 0,
            "token_compacted": 0,
            "session_id": session_id or "",
            "session_reset": False,
            "iterations": 0,
            "tool_calls": 0,
            "compactions": 0,
        }

        try:
            spec = self._resolve_spec(agent_name)
        except AgentNotRegisteredError as exc:
            return self._envelope(False, None, FINISH_NOT_REGISTERED, meta, str(exc))

        meta["agent_name"] = spec.name

        try:
            tool_specs = normalize_tools(tools)
        except FrameworkError as exc:
            return self._envelope(False, None, FINISH_TOOL_ERROR, meta, str(exc))

        try:
            payload_data = self._validate_payload(spec, payload)
        except InputSchemaError as exc:
            return self._envelope(False, None, FINISH_INPUT_INVALID, meta, str(exc))

        session, session_reset = self._resolve_session(spec, session_id)
        meta["session_id"] = session.session_id
        meta["session_reset"] = session_reset

        agent = session.agent
        if agent is None:
            agent = self._create_agent(spec)
            session.agent = agent
        try:
            register_tools(agent, tool_specs)
        except Exception as exc:  # noqa: BLE001
            return self._envelope(False, None, FINISH_TOOL_ERROR, meta, str(exc))

        user_input = self._build_user_input(payload_data)
        final_text = ""

        try:
            for iteration in range(self.max_tool_iterations):
                meta["iterations"] = iteration + 1

                if iteration == 0:
                    final_text = await agent.run(user_input, max_turns=self.max_turns)
                else:
                    final_text = await agent.resume(max_turns=self.max_turns)

                # <|tool_call|> 标记协议兜底：兼容不走原生工具调用的模型
                marker_call = parse_tool_call(final_text)
                if marker_call is None:
                    break

                meta["tool_calls"] += 1
                try:
                    tool = find_tool(tool_specs, marker_call.name)
                    tool_result = await execute_tool(tool, marker_call.arguments)
                except (ToolNotFoundError, ToolExecutionError) as exc:
                    return self._envelope(False, None, FINISH_TOOL_ERROR, meta, str(exc))

                agent.messages.append(
                    {
                        "role": "user",
                        "content": "工具 {0} 执行结果：{1}".format(marker_call.name, tool_result),
                        "created_at": int(time.time()),
                    }
                )
            else:
                return self._envelope(
                    False,
                    None,
                    FINISH_COMPACT,
                    meta,
                    "工具调用轮次超过上限 {0}".format(self.max_tool_iterations),
                )
        except FrameworkError as exc:
            return self._envelope(False, None, FINISH_ERROR, meta, str(exc))
        except Exception as exc:  # noqa: BLE001
            return self._envelope(
                False, None, FINISH_ERROR, meta, "{0}: {1}".format(type(exc).__name__, exc)
            )

        # ---- token 统计与上下文压缩 ----
        native_tool_calls = len(
            [m for m in getattr(agent, "messages", []) or [] if m.get("role") == "tool"]
        )
        meta["tool_calls"] = meta["tool_calls"] + native_tool_calls
        token_original = self._count_tokens(agent)
        meta["token_original"] = token_original
        meta["token_compacted"] = token_original

        compact_config = self._compact_config(spec)
        if compact_config.get("enabled", True) and token_original > int(
            compact_config.get("max_prompt_tokens", DEFAULT_COMPACT_CONFIG["max_prompt_tokens"])
        ):
            try:
                compacted = await self._compact_agent(agent)
                if compacted is False:
                    meta["compact_error"] = "压缩未生效（引擎返回 False）"
                else:
                    meta["compactions"] = 1
                meta["token_compacted"] = self._count_tokens(agent)
            except Exception as exc:  # noqa: BLE001 - 压缩失败不阻塞主流程，但需可观测
                meta["compact_error"] = "{0}: {1}".format(type(exc).__name__, exc)

        # ---- 最终输出校验 ----
        payload_obj = extract_json_payload(final_text)
        if payload_obj is None:
            return self._envelope(
                False,
                None,
                FINISH_OUTPUT_INVALID,
                meta,
                "模型输出无法解析为 JSON: {0}".format((final_text or "")[:200]),
            )

        if isinstance(payload_obj, list):
            payload_obj = {"items": payload_obj}

        try:
            output = model_validate(spec.output_model, payload_obj)
        except Exception as exc:  # noqa: BLE001 - pydantic 校验失败
            return self._envelope(
                False,
                None,
                FINISH_OUTPUT_INVALID,
                meta,
                "模型输出不符合 {0}: {1}".format(spec.output_model.__name__, exc),
            )

        finish_reason = FINISH_RESET if session_reset else FINISH_STOP
        return self._envelope(True, output, finish_reason, meta, "")

    # ------------------------------------------------------------------ 内部方法
    def _resolve_spec(self, agent_name: str) -> AgentSpec:
        """解析 agent 声明。"""
        getter = getattr(self.group, "get_agent_spec", None)
        if getter is None:
            raise AgentNotRegisteredError("当前对象不是 BaseAgentGroup")
        return getter(agent_name)

    def _validate_payload(self, spec: AgentSpec, payload: Any) -> Dict[str, Any]:
        """校验入参并转为 dict。"""
        if payload is None:
            raise InputSchemaError("payload 不能为空")
        if isinstance(payload, spec.input_model):
            return model_dump(payload)
        if isinstance(payload, dict):
            try:
                return model_dump(model_validate(spec.input_model, payload))
            except Exception as exc:  # noqa: BLE001
                raise InputSchemaError(
                    "入参不符合 {0}: {1}".format(spec.input_model.__name__, exc)
                )
        raise InputSchemaError(
            "payload 必须是 {0} 实例或 dict，当前为 {1}".format(
                spec.input_model.__name__, type(payload).__name__
            )
        )

    def _compact_config(self, spec: AgentSpec) -> Dict[str, Any]:
        """合并压缩配置：agent 声明优先，其次全局配置，最后默认值。"""
        config = dict(DEFAULT_COMPACT_CONFIG)
        if self.global_compact_config:
            config.update({k: v for k, v in self.global_compact_config.items() if v is not None})
        if spec.compact_config:
            config.update({k: v for k, v in spec.compact_config.items() if v is not None})
        return config

    def _resolve_session(self, spec: AgentSpec, session_id: Optional[str]):
        """获取或创建会话（会话内复用同一个 tinyagent 实例，ttl 到期即重置）。"""
        if not session_id:
            session = self.session_store.create(
                spec.name, spec.ttl, agent=self._create_agent(spec)
            )
            return session, False

        existing = self.session_store.peek(session_id)
        if existing is None:
            # 全新会话
            session = self.session_store.create(
                spec.name, spec.ttl, session_id=session_id, agent=self._create_agent(spec)
            )
            return session, False

        if existing.expired:
            # 会话过期 -> 重置
            session = self.session_store.create(
                spec.name, spec.ttl, session_id=session_id, agent=self._create_agent(spec)
            )
            return session, True

        session = existing
        if session.agent_name != spec.name:
            session = self.session_store.reset(
                session_id, spec.name, spec.ttl, agent=self._create_agent(spec)
            )
            return session, True

        session.touch(spec.ttl)
        return session, False

    async def _compact_agent(self, agent: Any) -> bool:
        """执行压缩。

        引擎在生成摘要时用 dict 方式读取 tool_calls，而运行期消息里是对象，
        这里临时换成可读形式；压缩失败时恢复原消息。
        """
        original = getattr(agent, "messages", []) or []
        try:
            agent.messages = messages_for_summary(list(original))
        except Exception:  # pragma: no cover - 转换失败时按原样压缩
            agent.messages = original

        try:
            return await agent.compact()
        except Exception:
            if getattr(agent, "messages", None) is not original:
                agent.messages = original
            raise

    def _create_agent(self, spec: AgentSpec) -> Any:
        """按 agent 声明创建 tinyagent 实例。"""
        from tinyagent import TinyAgent

        return TinyAgent(
            model=self.model,
            api_key=self.api_key,
            system_prompt=self._build_system_prompt(spec),
            temperature=self.temperature,
            model_kwargs=dict(self.model_kwargs),
            enable_todo_write=self.enable_todo_write,
            parallel_tool_calls=False,
            enable_custom_instruction=False,
            storage=self.storage,
            logger=self.logger,
        )

    def _build_system_prompt(self, spec: AgentSpec) -> str:
        """组装 system prompt：业务指令 + 输出约束 + 工具调用协议。"""
        instruction = spec.business_instruction or (
            "你是 {0} 助手，请根据用户输入完成任务。".format(spec.name)
        )
        blocks = [
            instruction,
            "\n【输出要求】\n最终仅输出符合以下 JSON Schema 的 JSON 对象，"
            "不要附带任何解释、说明或 Markdown 标记：\n" + json_dumps(spec.output_schema),
            "\n【工具调用】\n需要外部信息时必须调用工具，禁止凭空编造；"
            "一次只能调用一个工具。可使用原生函数调用，或使用以下标记协议：\n"
            "<|tool_call|>\n"
            '{"name": "工具名称", "arguments": {}}\n'
            "<|end_tool_call|>",
        ]
        return "\n".join(blocks)

    def _build_user_input(self, payload_data: Dict[str, Any]) -> str:
        """把入参转成用户消息（含当前日期，便于模型推导“明天”等相对日期）。"""
        now = datetime.now()
        return (
            "当前日期时间：{0}（{1}）\n"
            "如需相对日期（如今天/明天/后天），以此为基准换算。\n"
            "用户输入：\n{2}"
        ).format(
            now.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%A"),
            json_dumps(payload_data),
        )

    @staticmethod
    def _count_tokens(agent: Any) -> int:
        """统计当前会话的 token 数。"""
        try:
            total = 0
            for message in getattr(agent, "messages", []) or []:
                content = message.get("content") or ""
                total += agent.count_tokens(content)
            return total
        except Exception:  # pragma: no cover
            return 0

    @staticmethod
    def _envelope(
        ok: bool,
        data: Any,
        finish_reason: str,
        meta: Dict[str, Any],
        error: str,
    ) -> Dict[str, Any]:
        """构造框架统一返回结构。"""
        envelope: Dict[str, Any] = {
            "ok": ok,
            "data": data,
            "finish_reason": finish_reason,
            "meta": {
                "agent_name": meta.get("agent_name", ""),
                "token_original": meta.get("token_original", 0),
                "token_compacted": meta.get("token_compacted", 0),
                "session_id": meta.get("session_id", ""),
                "session_reset": meta.get("session_reset", False),
                "iterations": meta.get("iterations", 0),
                "tool_calls": meta.get("tool_calls", 0),
                "compactions": meta.get("compactions", 0),
            },
        }
        if meta.get("compact_error"):
            envelope["meta"]["compact_error"] = meta["compact_error"]
        if error:
            envelope["error"] = error
        return envelope
