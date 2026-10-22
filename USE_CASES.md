# 使用场景详解

本文说明 `zhkj_llm_function` 适合做什么、不适合做什么，每类场景给出可落地的写法与注意事项。
API 细节见 [README.md](README.md)，对外介绍见 [INTRO.md](INTRO.md)。

---

## 0. 场景总览

| 场景 | 是否适合 | 关键支撑 |
| --- | --- | --- |
| 多业务共用同一类能力，只换数据源 | ✅ 最推荐 | 工具外置 + schema 自动反射 |
| 把 LLM 输出变成强类型结果，嵌入既有 async 服务 | ✅ | 出参模型实例 + 输出校验 |
| 模型输出不可控时，让失败"可预期" | ✅ | 统一异常 + `error_message` 兜底字段 |
| 多轮追问、上下文延续 | ✅ | `session_id` 复用引擎实例 |
| 多轮工具调用后上下文膨胀 | ✅ | 自动压缩 + `meta["compactions"]` |
| 查数据 → 按规则判断 → 给结论（行程、补货、风控） | ✅ | docstring 业务规则 + 工具返回结构化数据 |
| 一个插件承载多个 agent 能力 | ✅ | 一个类里多个 `@agent_def` |
| 离线跑通、CI 稳定 | ✅ | 伪造 `litellm.acompletion` |
| 模型/网关切换、灰度 | ✅ | `set_llm()` + `model_kwargs` |
| 跨重启保留会话历史 | ❌ | 会话仅在内存，需业务侧自行映射 |
| 多进程/多副本共享同一会话 | ❌ | 同上；需外部会话存储 |
| 长流程自主任务（几十步、需严格状态机） | ⚠️ | 工具轮次上限 `max_tool_iterations`，超出返回 `compact` |
| 需要精确数值计算/强一致状态 | ⚠️ | LLM 不适合；应下沉到工具或代码 |
| 需要图像/语音等多模态输入 | ⚠️ | 框架只透传文本消息 |

---

## 1. 多业务共用同一套 Agent 能力，只换底层数据源

**适用信号**：快递轨迹、订单状态、库存、物流、天气、工单……结构相似但数据源不同；每个业务方都要"查一下再回答"。

**做法**：插件只描述"需要什么能力"，工具由调用方提供。

```python
# 框架侧（一份插件，所有业务复用）
class CourierAgentPlugin(BaseAgentGroup):
    @agent_def(ttl=86400, compact_config=None)
    async def courier_trace_agent(self, payload, tools=None) -> CourierTraceOutput:
        """
        角色：快递物流轨迹查询助手。
        业务规则：
        1. 必须调用工具查询，禁止凭记忆输出物流状态。
        2. 工具失败时保留运单号回显，其余置空，原因写入 error_message。
        """
        ...

# 业务 A：接自研快递系统
tools_a = [{"name": "query_courier_waybill", "description": "查询运单状态", "func": self_courier_api}]

# 业务 B：接第三方快递开放平台
tools_b = [{"name": "query_courier_waybill", "description": "查询运单状态", "func": third_party_api}]

# 业务 C：换城市数据（同样是"轨迹"语义）
tools_c = [{"name": "query_courier_waybill", "description": "查询运单状态", "func": oms_api}]
```

**收益**：能力口径、输出结构、异常语义完全一致；换数据源不改插件；不同业务可各自演进工具实现。

**注意点**：工具名与语义要稳定（业务规则里写死了工具名），工具返回值尽量结构化，模型才好归一化。

---

## 2. 把 LLM 输出变成强类型结果，嵌入既有 async 服务

**适用信号**：后端接口需要稳定的 JSON 契约，前端/下游强依赖字段；不想在业务层到处 `json.loads` + `try/except`。

**做法**：返回值就是出参模型实例，可直接序列化、可被 Pydantic 再校验。

```python
@router.post("/courier/trace")
async def trace(payload: CourierTraceInput, request: Request):
    plugin = request.app.state.courier_plugin          # 全局复用一个实例
    result = await plugin.courier_trace_agent(payload, app_tools)
    return result.model_dump()        # 结构 == CourierTraceOutput 声明，不含框架字段
```

**收益**：
- 契约即代码：`CourierTraceOutput` 既是模型约束，也是接口文档；
- `model_dump()` 只含声明字段，不会把 `meta` 之类内部信息漏给前端；
- 需要排障时用 `plugin.run_envelope(...)` 拿 `token_original / tool_calls / iterations`。

**注意点**：`plugin` 实例建议单例复用（内部有会话与引擎），不要每次请求 `new` 一个。

---

## 3. 让"模型不听话"变成可预期的失败

**适用信号**：模型偶尔不按格式输出、被要求"不许编造"时仍可能胡编；不能让异常直接冒到 500。

**做法**：模型侧失败用异常表达，业务侧失败用出参里的 `error_message` 表达。

```python
from zhkj_llm_function import OutputSchemaError, ToolExecutionError

try:
    result = await plugin.courier_trace_agent(payload, tools)
except OutputSchemaError as exc:
    # 模型输出不符合出参模型：重试、降级或返回兜底
    return {"code": "MODEL_OUTPUT_INVALID", "message": str(exc)}
except ToolExecutionError as exc:
    return {"code": "UPSTREAM_FAILED", "message": str(exc)}
```

```python
# 业务规则里要求"查不到就明说"，配合出参兜底字段
class CourierTraceOutput(BaseModel):
    waybill_no: str
    current_status: str = ""      # 查不到时为空
    is_signed: bool = False
    error_message: str = ""       # 失败原因原文
```

```python
result = await plugin.courier_trace_agent(payload, tools)
if result.error_message:
    return {"code": "QUERY_FAILED", "message": result.error_message}
```

**注意点**：
- 异常 = 框架/协议层问题；`error_message` = 业务查询失败。两者不要混用，否则调用方分不清是"模型抽风"还是"数据源没有"。
- 需要区分"重试型失败"和"终态失败"时，看 `finish_reason`：`tool_error` / `output_schema_invalid` / `input_schema_invalid` / `not_registered` / `reset` / `stop`。

---

## 4. 多轮追问与上下文延续

**适用信号**：用户连续追问"那昨天那单呢""换个城市""再详细点"；需要模型记住上一轮的运单号、城市、口径。

**做法**：同一 `session_id` 复用同一个引擎实例，历史自动带入上下文。

```python
result = await plugin.courier_trace_agent({"waybill_no": "SF123"}, tools, session_id=f"user-{uid}")
result = await plugin.courier_trace_agent({"waybill_no": "SF123"}, tools, session_id=f"user-{uid}")  # 第二轮带历史
```

**收益**：第二轮模型看到的上下文包含第一轮的用户消息、工具调用与工具结果（`system / user / assistant / tool / assistant`）。

**注意点**：
- **不传 `session_id` 就是全新会话**，没有历史——这是最常见的误用；
- ttl 到期（`@agent_def(ttl=...)`，默认 86400 秒）后同一 ID 会重置，`finish_reason` 变成 `reset`；
- 会话在**进程内存**里：多副本部署时需要粘性路由，或由业务侧把 `session_id` 映射到自己的存储；
- 历史里的 `tool` 消息会持续占用上下文，靠自动压缩兜底（见下节）。

---

## 5. 多轮工具调用后的上下文膨胀

**适用信号**：一次任务要查多次（查天气 → 查景点 → 查门票），或长会话跑了很多轮，上下文越来越大、越来越慢、越来越贵。

**做法**：框架在每轮结束后统计 token，超阈值自动压缩。

```python
plugin = CourierAgentPlugin(
    model="gpt-4o-mini",
    api_key=api_key,
    compact_config={"enabled": True, "max_prompt_tokens": 8000},
)
```

```python
envelope = await plugin.run_envelope("courier_trace_agent", payload, tools=tools)
envelope["meta"]["token_original"]    # 压缩前
envelope["meta"]["token_compacted"]   # 压缩后
envelope["meta"]["compactions"]       # 本次是否触发压缩
envelope["meta"].get("compact_error") # 压缩失败原因（成功则无此字段）
```

**收益**：上下文长度有上界；压缩后只剩 `system + 摘要`，模型仍记得"在查什么"。

**注意点**：
- 压缩会额外调用一次模型做摘要（成本 + 延迟）；
- 压缩后早期细节会丢失，只保留摘要 —— 需要"完整逐条轨迹"时，应让工具一次性返回完整数据，而不是依赖多轮上下文；
- 压缩失败不阻塞主流程，原因写在 `meta["compact_error"]`，务必在监控里看这个字段。

---

## 6. "查数据 → 按规则判断 → 给结论"类任务

**适用信号**：结论依赖外部数据，且判断规则是确定的。典型：天气决定行程、库存决定补货、时效决定是否告警。

**做法**：把**判断规则写进 docstring**，把**数据来源放进工具**。

```python
@agent_def(ttl=86400, compact_config=None)
async def weather_plan_agent(self, payload, tools=None) -> WeatherPlanOutput:
    """
    角色：天气行程规划助手。

    业务规则：
    1. 必须调用工具查询天气，禁止凭记忆输出温度与降水概率。
    2. 降水概率 ≥ 0.5 或有雷暴时，该时段活动类型必须为 indoor。
    3. 最高气温 ≥ 33℃ 或最低气温 ≤ 5℃ 时减少户外时段。
    4. 每条行程必须写明 weather_basis（引用具体数值）与 reason。
    5. 工具失败时其余字段置空，原因写入 error_message。
    """
    ...
```

**收益**：规则是自然语言、可评审、可迭代；模型只负责"把规则套到数据上并组织成结构化结果"，不负责决策本身。

**注意点**：
- 规则要**可判定**：避免"尽量""建议偏保守"这类无法核对的表述，否则无法测试；
- 关键数值（温度、概率）要求工具返回，docstring 明确"禁止编造"；
- 框架会把**当前日期时间**注入用户输入，因此"规划明天"这类相对日期需求无需业务方自己换算。

---

## 7. 一个插件承载多个能力 / 多 agent 编排

**做法**：一个插件类里可以声明多个 `@agent_def`，用 `run(...)` 按名字分发。

```python
class OpsPlugin(BaseAgentGroup):
    @agent_def(ttl=86400, compact_config=None)
    async def trace_agent(self, payload: TraceIn, tools=None) -> TraceOut:
        """..."""
        ...

    @agent_def(ttl=600, compact_config=None)
    async def suggest_agent(self, payload: SuggestIn, tools=None) -> SuggestOut:
        """..."""
        ...

OpsPlugin.agent_names()   # ['suggest_agent', 'trace_agent']

# 动态分发
result = await ops.run("trace_agent", payload, tools=tools, session_id=sid)
```

**注意**：不同 agent 的 `ttl` 可以不同（短任务短会话）；跨插件协作可以由一个插件在运行时调用另一个插件的 `run(...)`，但要注意不要形成循环调用。

---

## 8. 离线测试与 CI

**适用信号**：不想在 CI 里烧模型 token，也不想让测试依赖网络。

**做法**：伪造 `litellm.acompletion`，按顺序返回预设响应。

```python
async def _acompletion(**kwargs):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"waybill_no": "SF1", "current_status": "已签收", "is_signed": True}),
            tool_calls=[]), finish_reason="stop")],
        usage=None,
    )

monkeypatch.setattr(litellm, "acompletion", _acompletion)

result = await plugin.courier_trace_agent({"waybill_no": "SF1"}, tools)
assert result.current_status == "已签收"
assert result.ok is True
```

**收益**：工具调用、输出校验、异常分支、压缩逻辑都能在 CI 里断言（见 `tests/test_tool_session_compact.py`）。

**注意点**：测试桩只需覆盖"模型返回什么"，工具可以是真实函数或桩；断言要落在**业务字段**上，不要断言 token 数等不稳定量。

---

## 9. 模型 / 网关切换与灰度

```python
plugin = CourierAgentPlugin(model="gpt-4o-mini", api_key=key)
plugin.set_llm({"model": "gpt-4o", "api_key": key2})     # 运行期切换
plugin = CourierAgentPlugin(
    model="moonshot-v1-8k",
    api_key=key,
    model_kwargs={"base_url": os.getenv("LLM_BASE_URL")},  # 自建网关/代理
)
```

**注意点**：`model_kwargs` 会透传给 litellm，网关地址、额外请求头、超时等都可以从这里注入；不同模型的 JSON 遵循度不同，切换后建议跑一遍回归用例。

---

## 10. 不适合的场景（请直接绕开）

| 场景 | 原因 | 替代做法 |
| --- | --- | --- |
| 需要跨重启 / 跨进程共享会话历史 | 会话仅在内存 | 业务侧把 `session_id` 映射到外部存储，或每次请求带全量上下文 |
| 几十步的长流程自主任务，需要严格状态机 | 工具轮次上限 `max_tool_iterations`（默认 8），超出直接返回 `compact` | 用工作流引擎编排，每步一个 agent |
| 需要精确计算、强一致写入 | LLM 不适合做数值与事务 | 计算/写入下沉到工具或业务代码，LLM 只做意图与表达 |
| 需要图像、音频、视频输入 | 框架只透传文本消息 | 走多模态模型直连，或在工具里做预处理 |
| 需要"绝不失败"的强 SLA | LLM 调用本身有超时/限流风险 | 用 `run_envelope` 区分失败原因，配合降级数据兜底 |
| 一个插件要在多副本间共享会话 | 内存会话不跨进程 | 粘性会话，或外部会话存储 |

---

## 附录：市场信号类场景（加密货币短期方向预测）

**适用信号**：需要把「最近新闻 + 历史涨幅」变成一份**结构化、可评审、可留痕**的方向性判断（研究用途）。

**做法**：三段职责分离。

```python
# 1) 量化：确定性工具算，模型不碰算术
compute_market_stats(symbol, prices, horizon_days, benchmark_prices)
#   -> momentum_pct / momentum_state / annualized_vol_pct / volatility_state
#      max_drawdown_pct / rsi14 / drift_pct_per_day / relative_strength_pct

# 2) 规则：写在 docstring（框架转 system prompt）
#   动量 strong_up/up/down/strong_down × 新闻方向 -> bullish/bearish/neutral
#   置信度上限：证据不足/背离/高波动 -> <= 0.5；neutral -> <= 0.5
#   预期只能给区间（"-x% ~ +y%"），禁止点目标价
#   必须给 risks 与 invalidation_conditions（可观测、可判定）
#   必须保留 disclaimer（不构成投资建议）

# 3) 输出：强类型
result.signal / result.confidence / result.expected_move_pct
result.volatility_pct / result.key_drivers[] / result.risks[] / result.invalidation_conditions[]
```

**收益**
- 数字来源可审计：每个数值都能回溯到工具返回值，不存在"模型算错了"。
- 判断口径一致：同一份数据，不同人/不同模型跑出的规则判断一致，差异只体现在措辞。
- 结论自带风控位：`invalidation_conditions` 强制写出"什么情况下这个判断就错了"。

**必须注意**
- 这类输出是**研究性信号**，不是收益承诺；对外展示务必带免责声明（示例里强制保留）。
- 新闻情绪 → 价格的映射极不稳定；模型容易把"一条快讯"放大成"趋势"。规则里必须限制置信度上限、并要求引用具体标题。
- 历史涨幅只描述过去，不构成预测依据；工具返回里已带 `note` 提示，输出中禁止把它写成"未来收益"。
- 数据不足（价格样本 < 15、无新闻且无法获取）时，规则强制 `neutral + 低置信度 + error_message`，而不是硬凑一个方向。
- 若要真正可交易，需要在此之上再加：回测框架、样本外验证、仓位与风控约束——**这些都不该由 LLM 负责**。

---

## 一句话选型建议

> **"我要接一个外部数据源，让模型基于它回答，并且结果要能被下游程序直接消费"** → 用这个框架。
> **"我要跑一个几十步的长流程 Agent"** → 用工作流引擎，这个框架只适合单次会话内的能力调用。
