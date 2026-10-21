"""zhkj_llm_function：基于 @agent_def 装饰器 + Pydantic 类型注解反射的 Agent 插件框架。

执行引擎：tinyagent-py（ReAct 循环、工具调用、上下文压缩、token 统计）。
框架层：@agent_def 声明、Input/Output 模型校验、外部工具注册、统一返回结构。

快速开始：

    from zhkj_llm_function import BaseAgentGroup, agent_def, ToolDef

    class MyPlugin(BaseAgentGroup):
        @agent_def(ttl=86400, compact_config=None)
        async def my_agent(self, payload: MyInput, tools: list[ToolDef] | None = None) -> MyOutput:
            \"\"\"
            角色：xxx
            业务规则：
            1. xxx
            \"\"\"
            ...

    plugin = MyPlugin(model="gpt-5", api_key="...")
    result = await plugin.my_agent({"...": "..."}, tools=[...])   # 返回 MyOutput 实例
"""

from .agent import AgentSpec, BaseAgentGroup, DEFAULT_TTL, agent_def
from .errors import (
    AgentNotRegisteredError,
    FrameworkError,
    InputSchemaError,
    OutputSchemaError,
    SessionError,
    ToolExecutionError,
    ToolNotFoundError,
)
from .protocol import (
    TOOL_CALL_END,
    TOOL_CALL_START,
    ToolCall,
    extract_json_payload,
    parse_tool_call,
    render_tool_call,
)
from .result import build_result, envelope_to_result
from .runtime import DEFAULT_COMPACT_CONFIG, AgentRuntime
from .session import Session, SessionStore
from .tools import ToolDef, ToolSpec, execute_tool, normalize_tools, register_tools

__all__ = [
    "AgentSpec",
    "AgentRuntime",
    "BaseAgentGroup",
    "DEFAULT_TTL",
    "DEFAULT_COMPACT_CONFIG",
    "agent_def",
    "FrameworkError",
    "AgentNotRegisteredError",
    "InputSchemaError",
    "OutputSchemaError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "SessionError",
    "TOOL_CALL_START",
    "TOOL_CALL_END",
    "ToolCall",
    "parse_tool_call",
    "render_tool_call",
    "extract_json_payload",
    "build_result",
    "envelope_to_result",
    "Session",
    "SessionStore",
    "ToolDef",
    "ToolSpec",
    "normalize_tools",
    "register_tools",
    "execute_tool",
]

__version__ = "0.2.0"
