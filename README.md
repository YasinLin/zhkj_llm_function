# zhkj_llm_function（DeclareAgent 框架 / tinyagent-py 引擎）

基于 `@agent_def` 装饰器 + Pydantic 类型注解反射的 Agent 插件框架。
执行引擎使用 [tinyagent-py](https://pypi.org/project/tinyagent-py/)：ReAct 循环、工具调用、上下文压缩、token 统计。

包名与导入名一致：`pip install zhkj_llm_function` → `from zhkj_llm_function import ...`。

📄 对外介绍版（痛点、方案、架构、对比、路线图）见 [INTRO.md](INTRO.md)。

**特点**

- 声明式：插件里只写 `@agent_def` 方法 + Pydantic 入参/出参 + docstring 业务规则，方法体只写 `...`
- 返回值干净：直接调用拿到的就是**出参模型实例**，`model_dump()` 与出参签名完全一致，扩展信息走代理属性
- 工具外置：工具由调用方在运行时传入，参数 JSON Schema 由函数签名自动反射，插件与调用方都不用手写 schema
- 统一错误：校验失败、工具失败、agent 未注册都抛明确异常，不返回"半成品"对象
- 会话内自动带历史，超阈值自动压缩上下文

## 安装

```bash
# 使用方：直接安装已发布的包
pip install zhkj_llm_function

# 从源码安装（开发模式）
pip install -e .

# 安装开发依赖（pytest）
pip install -e ".[dev]"
```

环境要求：Python ≥ 3.10（`tinyagent-py` 依赖 `mcp`）。
依赖：`tinyagent-py>=0.1.20`、`pydantic>=2.0`。

发布到内部源时：

```bash
python -m build            # 产出 dist/*.whl 与 *.tar.gz
pip install dist/zhkj_llm_function-0.2.0-py3-none-any.whl
```

## 发布到包索引

```bash
# 1. 准备打包工具
pip install build twine

# 2. 清理并构建（产出 dist/*.whl 与 dist/*.tar.gz）
rm -rf dist build *.egg-info
python -m build

# 3. 校验元数据（必须 PASSED）
python -m twine check dist/*

# 4. 上传
python -m twine upload dist/*                        # PyPI 正式
python -m twine upload --repository testpypi dist/*   # TestPyPI 试发布
python -m twine upload --repository nexus dist/*      # 内部源（需先配置 ~/.pypirc）
```

凭据用环境变量 `TWINE_USERNAME` / `TWINE_PASSWORD`，或 `~/.pypirc`：

```ini
[distutils]
index-servers =
    pypi
    nexus

[pypi]
username = __token__
password = pypi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

[nexus]
repository = https://nexus.example.com/repository/pypi-hosted/
username = your-user
password = your-token
```

发布后使用方直接安装：

```bash
pip install zhkj_llm_function
```

首次发布前需先在目标索引占位注册同名项目。`dist/` 已在 `.gitignore` 中，不入库。

## 快速开始

### 1. 写插件

```python
from pydantic import BaseModel, Field
from zhkj_llm_function import BaseAgentGroup, ToolDef, agent_def


class CourierTraceInput(BaseModel):
    """快递轨迹查询入参"""

    waybill_no: str = Field(..., description="快递运单号")


class CourierTraceOutput(BaseModel):
    """快递轨迹查询出参"""

    current_status: str = Field(..., description="当前状态，如 已签收/运输中")
    is_signed: bool = Field(..., description="是否已签收")
    summary: str = Field(..., description="一句话中文结论")
    error_message: str = Field(..., description="失败原因，成功时为空字符串")


class CourierAgentPlugin(BaseAgentGroup):
    """快递物流轨迹查询插件。"""

    @agent_def(ttl=86400, compact_config=None)
    async def courier_trace_agent(
        self, payload: CourierTraceInput, tools: list[ToolDef] | None = None
    ) -> CourierTraceOutput:
        """
        角色：快递物流轨迹查询助手，负责查询运单最新状态与历史轨迹。

        业务规则：
        1. 必须调用工具查询，禁止凭记忆或推测输出物流状态。
        2. 工具调用失败时保留运单号回显，其余字段置为未知/空值，失败原因写入 error_message。
        3. 最终输出必须严格符合 CourierTraceOutput 结构，禁止附加自然语言解释。
        """
        ...
```

### 2. 定义工具（放在调用方，插件内不写工具）

```python
tools = [
    {
        "name": "query_courier_waybill",
        "description": "查询运单当前状态与轨迹列表",
        "func": query_courier_waybill,   # 支持同步/异步函数
    }
]
```

`func` 的**函数签名就是工具参数 schema 的唯一来源**，框架自动反射生成 JSON Schema：

```python
async def query_courier_waybill(waybill_no: str, include_history: bool = True) -> dict:
    """查询运单当前状态与轨迹列表"""
    ...
```

### 3. 调用

```python
plugin = CourierAgentPlugin(model="gpt-4o-mini", api_key=os.getenv("LLM_API_KEY"))

result = await plugin.courier_trace_agent(
    {"waybill_no": "SF123456"},           # 也可以传 CourierTraceInput 实例
    tools,
)

result.current_status      # "运输中"        ← 直接按出参签名取用
result.is_signed          # False
result.model_dump()       # 只有出参声明的字段
result.ok                 # True            ← 代理属性
result.finish_reason      # "stop"          ← 代理属性
result.meta               # token 统计等扩展信息 ← 代理属性
```

## 核心概念

### agent 声明

| 项 | 规则 |
| --- | --- |
| 方法签名 | `async def xxx_agent(self, payload: XXXInput, tools: list[ToolDef] \| None = None) -> XXXOutput` |
| 参数名 | 必须是 `self` / `payload` / `tools`（框架按位置校验，写错会直接报错） |
| 入参/出参 | 必须是 Pydantic `BaseModel` 子类 |
| 方法体 | 只写 `...`；业务规则写 docstring，框架自动转 system prompt |
| `ttl` | 会话有效期（秒），常用 `86400`（一天），必须是正整数 |
| `compact_config` | `None` 用默认压缩策略，也可传 `{"enabled": True, "max_prompt_tokens": 8000}` |

插件可以声明多个 agent：

```python
class MyPlugin(BaseAgentGroup):
    @agent_def(ttl=86400, compact_config=None)
    async def a_agent(self, payload: AIn, tools=None) -> AOut:
        """..."""
        ...

    @agent_def(ttl=600, compact_config=None)
    async def b_agent(self, payload: BIn, tools=None) -> BOut:
        """..."""
        ...

MyPlugin.agent_names()          # ['a_agent', 'b_agent']
MyPlugin.get_agent_spec("a_agent")  # AgentSpec：schema / ttl / 业务指令
```

### 工具

- 工具**一律由外部在调用时传入**，插件内不允许定义工具函数（保证插件可复用、可测试）。
- 工具定义形如 `{"name": str, "description": str, "func": callable}`；`description` 缺省时取函数 docstring。
- 参数 schema 由 `func` 签名自动反射（`city: str` → `{"type": "string"}`），**不需要手写**。
- 一次只允许一个工具；工具异常包装成 `ToolExecutionError`。
- 调用协议：优先 tinyagent 原生 function calling；模型不支持时兼容框架标记协议兜底：

```
<|tool_call|>
{"name": "query_courier_waybill", "arguments": {"waybill_no": "SF123"}}
<|end_tool_call|>
```

### 调用入口（三种等价写法）

```python
# 1) 普通方法方式（和普通 async 方法一样）
result = await plugin.xxx_agent(payload, tools)
result = await plugin.xxx_agent(payload, tools, session_id="s1")

# 2) 统一入口（按名字调用，动态分发）
result = await plugin.run("xxx_agent", payload, tools=tools, session_id="s1")

# 3) 需要框架原始封装（监控/排障用）
envelope = await plugin.run_envelope("xxx_agent", payload, tools=tools)
```

### 返回值语义

```python
result                 # XXXOutput 实例（动态子类），isinstance(result, XXXOutput) == True
result.model_dump()    # 只含声明字段，结构与出参签名完全一致
result.ok              # 代理属性：True
result.finish_reason   # 代理属性：stop / reset / compact ...
result.meta            # 代理属性：token 统计、session 信息等扩展信息
result.error           # 代理属性：失败原因（成功时为空）
```

扩展信息保存在 Pydantic 私有属性里，**不会污染出参 schema**，也不会出现在 `model_dump()` 中。

框架原始封装结构（`run_envelope`）：

```json
{
  "ok": true,
  "data": "<XxxOutput 实例>",
  "finish_reason": "stop",
  "meta": {
    "agent_name": "xxx_agent",
    "token_original": 1465,
    "token_compacted": 1465,
    "session_id": "...",
    "session_reset": false,
    "iterations": 1,
    "tool_calls": 1,
    "compactions": 0
  }
}
```

`finish_reason`：`stop` / `reset`（会话 ttl 过期重置）/ `compact`（工具轮次超限）/ `not_registered` /
`input_schema_invalid` / `output_schema_invalid` / `tool_error` / `error`。

### 会话与压缩

会话只存在于**进程内存**中（框架不落盘、不做持久化）：

- 不传 `session_id`：每次调用都是全新会话，不带任何历史。
- 传同一个 `session_id`：在 ttl 内复用同一个引擎实例，历史自动带入上下文。
- ttl 到期后再次使用同一 `session_id`：会话重置（`finish_reason="reset"`），历史清空。
- 进程重启后历史即失效；如需跨重启保留，请由业务侧自行把 `session_id` 映射到外部存储。

上下文压缩：

- 上下文 token 超过 `max_prompt_tokens`（默认 8000）时自动调用 `agent.compact()` 压缩。
- 压缩会先做一次摘要（额外一次模型调用），压缩后上下文只剩 `system + 摘要`。
- 压缩失败不阻塞主流程，原因写入 `meta["compact_error"]`。
- 发生过工具调用的会话也能正常压缩：框架在压缩前把 `tool_calls` 转成可读形式（引擎按 dict 读取，运行期却是对象）。

```python
plugin = XxxPlugin(
    model="gpt-4o-mini",
    api_key=api_key,
    compact_config={"enabled": True, "max_prompt_tokens": 8000},
    max_tool_iterations=8,   # 工具调用轮次上限
    max_turns=10,            # 单轮 ReAct 轮数上限
)
```

### 错误处理

直接调用与 `run(...)` 在失败时**抛异常**，不返回空对象：

| 异常 | 场景 |
| --- | --- |
| `AgentNotRegisteredError` | agent 未注册 |
| `InputSchemaError` | 入参不符合声明模型 |
| `OutputSchemaError` | 模型输出不符合声明模型 |
| `ToolExecutionError` | 工具缺失或执行失败 |
| `FrameworkError` | 其他运行时错误（基类） |

```python
from zhkj_llm_function import OutputSchemaError

try:
    result = await plugin.xxx_agent(payload, tools)
except OutputSchemaError as exc:
    ...
```

## 构造参数

```python
plugin = XxxPlugin(
    model="gpt-4o-mini",
    api_key=os.getenv("LLM_API_KEY"),
    model_kwargs={"base_url": os.getenv("LLM_BASE_URL")},  # 自定义网关/代理
    temperature=1,
    session_store=None,        # 自定义会话存储（默认内存）
    compact_config=None,       # 压缩策略
    max_tool_iterations=8,
    max_turns=10,
    enable_todo_write=False,
    storage=None,              # 透传给 tinyagent 的 storage
    logger=None,
)
```

`set_llm()` 可在运行期切换模型/密钥：

```python
plugin.set_llm({"model": "gpt-4o", "api_key": "sk-xxx"})
```

## 示例

仓库内含两个可直接运行的示例插件：

### 快递轨迹查询

- 插件：`plugins/courier_plugin.py`（`courier_trace_agent`）
- 运行：`.venv\Scripts\python.exe examples\run_demo.py`
  （未设置 `LLM_API_KEY` 时走内置假模型，可离线跑通）

### 结合「明天」天气规划明天行程

- 插件：`plugins/weather_plan_plugin.py`（`weather_plan_agent`）
- 外部工具：`examples/weather_tools.py`（`query_weather_forecast`，数据源 Open-Meteo，免费无需 key）
- 运行：`.venv\Scripts\python.exe examples\run_weather_plan.py`

```python
result = await plugin.weather_plan_agent({"city": "杭州"}, tools)
# result.plan_date / result.weather_summary / result.temperature_range
# result.precipitation_probability_max / result.has_heavy_rain / result.plan[] / result.suggestions[]
```

业务规则示例（写在 docstring，框架自动转 system prompt）：
必须调用天气工具、禁止编造；`plan_date` 取 `payload.date` 或当前日期次日（框架会把当前日期注入用户输入）；
降水概率 ≥ 0.5 或雷暴/雨雪时该时段改为室内；≥ 33℃ 或 ≤ 5℃ 减少户外；按上午/下午/晚上给出 ≥ 3 条行程，
每条写明天气依据与理由。

## 目录结构

```
zhkj_llm_function/       # 框架层（可安装的包）
  __init__.py             # 对外导出
  agent.py                # agent_def 装饰器 / AgentSpec / BaseAgentGroup
  runtime.py              # 运行时：驱动 tinyagent，产出统一返回结构
  tools.py                # 工具归一化 + 自动反射参数 schema + 执行
  session.py              # 会话存储（内存 + ttl 管控）
  schema.py               # Pydantic 模型 schema / docstring 解析
  protocol.py             # <|tool_call|> 标记协议与 JSON 提取
  compat.py               # tinyagent / litellm 消息对象兼容转换
  result.py               # 出参代理结果对象
  errors.py               # 异常定义
plugins/                  # 示例插件（业务侧参考）
examples/                 # 可运行示例
tests/                    # 端到端测试
```

## 开发与测试

```bash
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"

python -m pytest tests -q          # 全部用例
python -m pytest tests/test_tool_session_compact.py -v   # 工具调用 + 会话 + 压缩
python examples/run_demo.py        # 离线端到端示例
```

测试不依赖真实模型：伪造 `litellm.acompletion` 模拟 OpenAI 风格响应，因此可在 CI 稳定运行。

## 常见问题

**Q：为什么工具要外置？**
插件里写死工具会让插件与具体数据源耦合、无法在测试中替换。工具外置后，插件只声明"需要什么能力"，
由调用方决定接哪个数据源，插件可以被多个业务复用。

**Q：为什么返回值不是 `{ok, data, ...}`？**
业务方通常只关心业务字段。直接返回出参实例可以让调用方按出参签名写代码；
`ok / finish_reason / meta` 这些框架信息通过代理属性提供，既不丢信息，也不污染出参结构。
需要原始封装时用 `run_envelope(...)`。

**Q：模型输出不是合法 JSON 怎么办？**
会抛 `OutputSchemaError`（`run_envelope` 里则是 `finish_reason="output_schema_invalid"`）。
建议在业务规则里明确要求"仅输出结构化 JSON，禁止附加解释"。

**Q：如何接自建网关 / 代理？**
通过 `model_kwargs={"base_url": ...}` 透传给 litellm；示例里用的是 `LLM_BASE_URL` 环境变量。

**Q：会话会一直增长吗？**
会话在内存中且受 ttl 约束，过期即重置；单轮内上下文超过 `max_prompt_tokens` 会自动压缩。
