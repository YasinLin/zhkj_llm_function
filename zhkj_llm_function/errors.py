"""框架内部异常类型。"""


class FrameworkError(Exception):
    """所有框架异常的基类。"""


class AgentNotRegisteredError(FrameworkError):
    """请求的 agent 未在 BaseAgentGroup 子类中注册。"""


class InputSchemaError(FrameworkError):
    """入参不符合 agent 声明的 Input 模型。"""


class OutputSchemaError(FrameworkError):
    """模型最终输出不符合 agent 声明的 Output 模型。"""


class ToolExecutionError(FrameworkError):
    """工具执行过程中抛出异常。"""


class ToolNotFoundError(FrameworkError):
    """模型请求了未传入的工具。"""


class SessionError(FrameworkError):
    """会话相关的错误。"""
