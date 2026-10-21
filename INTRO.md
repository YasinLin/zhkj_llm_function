# zhkj_llm_function · 项目介绍

> 面向 GitHub / 内部分享的对外介绍版；使用细节见 [README.md](README.md)。

## 项目简介

`zhkj_llm_function` 是一个基于 [tinyagent-py](https://pypi.org/project/tinyagent-py/) 的声明式 Agent 插件框架。业务侧用 `@agent_def` 装饰一个 async 方法、用 Pydantic 模型声明入参与出参、把业务规则写进 docstring，方法体只写 `...`；调用时把工具传进来，框架自动反射工具参数 schema、驱动引擎执行、校验输出，最终返回的就是**出参模型实例**——`model_dump()` 与出参签名完全一致，`ok / finish_reason / meta` 等框架信息通过代理属性提供，不污染业务结构。

框架把接 LLM 时重复的胶水代码（prompt 拼接、参数校验、JSON 解析、异常兜底、会话历史、上下文裁剪）收敛成声明式的一套约定：工具外置让插件与数据源解耦、可复用；统一异常让失败可按类型捕获；会话按 `session_id` 复用引擎实例并按 ttl 重置；上下文超阈值自动压缩且压缩结果可观测。适合需要"多业务共用同一套 Agent 能力、只替换底层数据源"的后端服务。

## 一句话

**基于 tinyagent-py 的声明式 Agent 插件框架**：`@agent_def` 声明能力、Pydantic 类型反射入参出参、工具由调用方外置传入，调用拿到手的就是**出参模型实例**。

```python
result = await plugin.courier_trace_agent({"waybill_no": "SF1234567890"}, tools)
result.current_status      # "已签收"
result.ok                  # True（代理属性，不污染出参结构）
```

## 我们要解决的问题

接 LLM 落到业务里，反复出现的是同一批工程问题：

| 痛点 | 现状 | 框架的答案 |
| --- | --- | --- |
| 每个 Agent 都重写一遍胶水代码 | prompt 拼接、参数校验、JSON 解析、异常兜底各写一遍 | `@agent_def` 声明 + 类型注解反射，方法体只写 `...` |
| Agent 代码与数据源耦合 | 换一家快递/天气 API 就要改 agent | 工具**外置**，插件只声明"需要什么能力" |
| 返回结构不统一 | 有的返回 dict、有的 tuple，框架字段混进业务字段 | 返回值即出参模型实例；`ok / finish_reason / meta` 走代理属性 |
| 失败处理含糊 | 模型输出不合法时返回一个半成品对象 | 统一异常：`InputSchemaError` / `OutputSchemaError` / `ToolExecutionError` |
| 会话与上下文失控 | 历史要么丢、要么无限增长撑爆上下文 | 同一 `session_id` 自动带历史；超阈值自动压缩，压缩行为可观测 |

## 核心能力

- **声明式插件**：能力 = 一个 `@agent_def` async 方法；入参/出参 = Pydantic 模型；业务规则 = docstring（自动转 system prompt）。
- **工具自动反射**：`func` 的函数签名就是参数 JSON Schema 的唯一来源，插件与调用方都不手写 schema。
- **双协议兼容**：优先 tinyagent 原生 function calling；模型不支持时回落到框架标记协议 `<|tool_call|>{...}<|end_tool_call|>`。
- **干净返回值**：`isinstance(result, XXXOutput) == True`，`model_dump()` 与出参签名完全一致，扩展信息在私有属性里。
- **会话与压缩**：进程内会话按 ttl 复用引擎实例；上下文超阈值自动 `compact()`，压缩失败原因写入 `meta["compact_error"]`。
- **可测试**：不依赖真实模型，伪造 `litellm.acompletion` 即可端到端跑，CI 稳定。

## 架构

```
┌──────────────────────── 调用方（业务） ────────────────────────┐
│  tools=[{"name": ..., "func": ...}]     payload（dict/模型）    │
└───────────────┬──────────────────────────────────┬──────────────┘
                │                                  │
        ┌───────▼────────┐                 ┌───────▼────────┐
        │  插件层         │                 │  工具层         │
        │ @agent_def      │                 │ 外部函数        │
        │ Input/Output   │                 │ schema 自动反射  │
        └───────┬────────┘                 └────────────────┘
                │
        ┌───────▼──────────────────────────────────────────┐
        │  框架层 zhkj_llm_function                        │
        │  runtime  执行 + 校验 + 压缩 + 返回封装           │
        │  result   出参代理对象（ok/finish_reason/meta）   │
        │  session  内存会话 + ttl                          │
        │  protocol 原生工具调用 + 标记协议兜底              │
        └───────┬──────────────────────────────────────────┘
                │
        ┌───────▼────────┐
        │  tinyagent-py  │  ReAct 循环 / 工具执行 / 压缩 / token 统计
        └───────┬────────┘
                │
          litellm → OpenAI 兼容网关
```

## 快速开始

```python
# 1) 定义插件
class CourierAgentPlugin(BaseAgentGroup):
    @agent_def(ttl=86400, compact_config=None)
    async def courier_trace_agent(
        self, payload: CourierTraceInput, tools: list[ToolDef] | None = None
    ) -> CourierTraceOutput:
        """
        角色：快递物流轨迹查询助手。
        业务规则：
        1. 必须调用工具查询，禁止凭记忆或推测输出物流状态。
        2. 工具失败时保留运单号回显，其余置空，原因写入 error_message。
        """
        ...

# 2) 传工具（函数签名即 schema）
tools = [{"name": "query_courier_waybill",
          "description": "查询运单状态与轨迹",
          "func": query_courier_waybill}]

# 3) 调用
plugin = CourierAgentPlugin(model="gpt-4o-mini", api_key=os.getenv("LLM_API_KEY"))
result = await plugin.courier_trace_agent({"waybill_no": "SF1234567890"}, tools)
```

## 与常见做法的对比

| 维度 | 常见做法 | 本框架 |
| --- | --- | --- |
| 能力定义 | 到处散落拼 prompt 的函数 | 一个带类型注解的 async 方法 |
| 入参/出参 | 手工校验 + 手工解析 JSON | Pydantic 模型，框架自动校验与解析 |
| 工具 | 硬编码在 agent 里 | 调用方运行时传入，schema 自动反射 |
| 返回值 | 业务字段与框架字段混在一个 dict | 出参实例 + 代理属性（`model_dump` 干净） |
| 失败 | 返回 `{"error": ...}` 或抛裸异常 | 语义化异常类型，可按类型捕获 |
| 会话 | 手动维护历史 | `session_id` 自动复用与重置 |
| 上下文 | 手动裁剪 | 超阈值自动压缩，压缩结果可观测 |

## 工程现状

- 框架代码约 1150 行（10 个模块），测试约 500 行 / 23 个用例，全部离线可跑。
- 依赖仅两个：`tinyagent-py`、`pydantic`；Python ≥ 3.10。
- 已内置示例：快递轨迹查询、结合"明天"天气规划明天行程（Open-Meteo 免费接口）。

## 适用场景

- 需要**多个业务共用同一套 Agent 能力**、只替换底层数据源（快递、天气、订单、库存……）。
- 需要**返回值强类型**、能被上层代码直接消费的后端接口。
- 需要**把 LLM 能力嵌进既有 async 服务**、并要求错误可分类、可观测的团队。

## 路线图（规划中）

- 会话状态可选外置（Redis / DB），框架只保留接口，不强制实现。
- 结构化输出多 schema 候选与自动重试（输出校验失败时带错误信息回灌重试一次）。
- 工具并发开关、工具调用预算与限流。
- 观测接入：调用链 trace、token/耗时指标导出。

## 参与

```bash
pip install -e ".[dev]"
python -m pytest tests -q
python examples/run_demo.py          # 离线端到端示例
```

欢迎提 issue / PR，尤其欢迎补充新示例插件与真实数据源适配。
