# zhkj_llm_function (DeclareAgent framework / tinyagent-py engine)

[English](README.md) · [中文](README_CN.md) · [Project Intro (EN)](INTRO_EN.md) · [项目介绍（中文）](INTRO.md) · [使用场景详解（中文）](USE_CASES.md)

A declarative agent plugin framework built on `@agent_def` decorators and Pydantic type annotations.
The execution engine is [tinyagent-py](https://pypi.org/project/tinyagent-py/): ReAct loop, tool calls, context compaction, token accounting.

The distribution name and the import name are identical: `pip install zhkj_llm_function` → `from zhkj_llm_function import ...`.

📄 [Project intro (EN)](INTRO_EN.md) covers pain points, architecture, comparisons and roadmap; the [中文版项目介绍](INTRO.md) and [使用场景详解（中文）](USE_CASES.md) cover scenarios and trade-offs in depth.

**Why it is different**

- **Declarative**: a plugin is an `@agent_def` async method + Pydantic input/output models + business rules in the docstring. The method body is just `...`
- **Clean return values**: you get the **output model instance**; `model_dump()` matches the declared output schema exactly, and framework metadata is exposed through proxy properties
- **Tools are injected**: tools are passed in by the caller at call time; the parameter JSON Schema is reflected from the function signature, so nobody hand-writes schemas
- **Explicit failures**: schema validation, tool failures and unregistered agents raise typed exceptions instead of returning half-built objects
- **Sessions and compaction**: history is carried inside a session, and the context is compacted automatically once it exceeds the threshold

## What you can build with it

The framework is domain-agnostic: any task shaped as "**fetch data → apply rules → emit a structured conclusion**" fits.

| Area | Example task | Keywords |
| --- | --- | --- |
| **Analysis & forecasting** | Trend forecast with a directional call, a range and invalidation conditions | predictive analysis, time-series forecasting, signal generation, prediction range, confidence calibration |
| **Crypto & digital assets** | Short-term crypto signal from candles + news, with volatility and risk notes | crypto, cryptocurrency, bitcoin, BTC/ETH, crypto price prediction, crypto signal, volatility, RSI, momentum |
| **Financial markets** | Quote summaries, price-move attribution, portfolio risk alerts | stock analysis, market analysis, attribution, investment research assistant, risk alerts |
| **News & monitoring** | Crawl, de-duplicate, classify sentiment, aggregate anomaly signals | news crawling, sentiment analysis, text classification, information extraction, monitoring and alerting |
| **Data & reporting** | Heterogeneous sources → structured reports, summaries, dashboard copy | data analysis, report generation, auto summary, data insight, metric explanation |
| **Knowledge & retrieval** | Knowledge-base Q&A with grounded answers | knowledge base QA, RAG, retrieval augmented generation, document QA, customer support |
| **Business automation** | Fetch → decide → act → acknowledge across systems | workflow orchestration, tool-calling automation, ticket handling, order processing, reconciliation |
| **Content & documents** | Structured extraction, drafting, translation, proofreading | document extraction, information extraction, summarization, content generation, compliance check |
| **Conversational products** | Multi-turn sessions, context carry-over, intent routing | multi-turn dialogue, context management, session memory, intent detection |
| **Engineering & evaluation** | Offline agent regression tests and output validation | llm testing, regression testing, offline evaluation, structured output validation, CI integration |

Keywords: `agent framework`, `declarative agent`, `llm agent`, `agent plugin`, `tool calling`, `function calling`,
`structured output`, `typed result`, `pydantic schema reflection`, `multi-turn session`, `context compaction`,
`llm testing`, `predictive analysis`, `time series forecasting`, `crypto price prediction`, `crypto signal`,
`news sentiment analysis`, `market data analysis`, `anomaly detection`, `report generation`, `rag chatbot`,
`workflow automation`.

## Installation

```bash
# Consumers: install the published package
pip install zhkj_llm_function

# From source (development mode)
pip install -e .

# With development dependencies (pytest)
pip install -e ".[dev]"
```

Requirements: Python >= 3.10 (`tinyagent-py` depends on `mcp`).
Dependencies: `tinyagent-py>=0.1.20`, `pydantic>=2.0`.

When publishing to an internal index:

```bash
python -m build            # produces dist/*.whl and *.tar.gz
pip install dist/zhkj_llm_function-0.2.0-py3-none-any.whl
```

## Publishing to a package index

```bash
# 1. Install the packaging tools
pip install build twine

# 2. Clean and build (produces dist/*.whl and dist/*.tar.gz)
rm -rf dist build *.egg-info
python -m build

# 3. Validate metadata (must report PASSED)
python -m twine check dist/*

# 4. Upload
python -m twine upload dist/*                        # PyPI
python -m twine upload --repository testpypi dist/*   # TestPyPI
python -m twine upload --repository nexus dist/*      # internal index (configure ~/.pypirc first)
```

Use the `TWINE_USERNAME` / `TWINE_PASSWORD` environment variables, or `~/.pypirc`:

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

After publishing, consumers install with:

```bash
pip install zhkj_llm_function
```

Reserve the project name on the target index before the first release. `dist/` is git-ignored.

## Quick start

### 1. Write the plugin

```python
from pydantic import BaseModel, Field
from zhkj_llm_function import BaseAgentGroup, ToolDef, agent_def


class CourierTraceInput(BaseModel):
    """Courier tracking query input"""

    waybill_no: str = Field(..., description="Courier waybill number")


class CourierTraceOutput(BaseModel):
    """Courier tracking query output"""

    current_status: str = Field(..., description="Current status, e.g. delivered / in transit")
    is_signed: bool = Field(..., description="Whether the parcel has been signed for")
    summary: str = Field(..., description="One-sentence summary")
    error_message: str = Field(..., description="Failure reason; empty string on success")


class CourierAgentPlugin(BaseAgentGroup):
    """Courier logistics tracking plugin."""

    @agent_def(ttl=86400, compact_config=None)
    async def courier_trace_agent(
        self, payload: CourierTraceInput, tools: list[ToolDef] | None = None
    ) -> CourierTraceOutput:
        """
        Role: courier logistics tracking assistant, responsible for the latest status and history of a waybill.

        Business rules:
        1. You must call the tool to query; never output a logistics status from memory or guesswork.
        2. If the tool call fails, keep echoing the waybill number, leave the remaining fields unknown/empty, and write the reason into error_message.
        3. The final output must strictly match the CourierTraceOutput structure; no extra natural-language explanation.
        """
        ...
```

### 2. Define the tools (in the caller, never inside the plugin)

```python
tools = [
    {
        "name": "query_courier_waybill",
        "description": "Query the current status and the tracking history of a waybill",
        "func": query_courier_waybill,   # sync or async functions are supported
    }
]
```

The **function signature is the single source of truth** for the tool's parameter schema; the framework reflects it into JSON Schema:

```python
async def query_courier_waybill(waybill_no: str, include_history: bool = True) -> dict:
    """Query the current status and the tracking history of a waybill"""
    ...
```

### 3. Call it

```python
plugin = CourierAgentPlugin(model="gpt-4o-mini", api_key=os.getenv("LLM_API_KEY"))

result = await plugin.courier_trace_agent(
    {"waybill_no": "SF123456"},           # a CourierTraceInput instance also works
    tools,
)

result.current_status      # "in transit"   <- exactly the declared output fields
result.is_signed           # False
result.model_dump()        # only the declared output fields
result.ok                  # True           <- proxy property
result.finish_reason       # "stop"         <- proxy property
result.meta                # token accounting and other metadata <- proxy property
```

## Core concepts

### Agent declaration

| Item | Rule |
| --- | --- |
| Method signature | `async def xxx_agent(self, payload: XXXInput, tools: list[ToolDef] \| None = None) -> XXXOutput` |
| Parameter names | Must be `self` / `payload` / `tools` (validated positionally; a typo fails fast) |
| Input / output models | Must be Pydantic `BaseModel` subclasses |
| Method body | `...` only; business rules live in the docstring, which becomes the system prompt |
| `ttl` | Session time-to-live in seconds (e.g. `86400` for one day); must be a positive integer |
| `compact_config` | `None` uses the default policy; you may pass `{"enabled": True, "max_prompt_tokens": 8000}` |

A plugin may declare several agents:

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

MyPlugin.agent_names()            # ['a_agent', 'b_agent']
MyPlugin.get_agent_spec("a_agent")  # AgentSpec: schemas / ttl / business instructions
```

### Tools

- Tools are **always supplied by the caller at call time**; plugins must not define tool functions (this keeps plugins reusable and testable).
- A tool is `{"name": str, "description": str, "func": callable}`; when `description` is omitted, the function docstring is used.
- The parameter schema is reflected from the `func` signature (`city: str` → `{"type": "string"}`); **nothing to write by hand**.
- One tool call at a time; tool exceptions are wrapped in `ToolExecutionError`.
- Calling protocol: native tinyagent function calling first; when the model does not support it, the framework falls back to its tagged protocol:

```
<|tool_call|>
{"name": "query_courier_waybill", "arguments": {"waybill_no": "SF123"}}
<|end_tool_call|>
```

### Three equivalent call styles

```python
# 1) Plain method call (like any other async method)
result = await plugin.xxx_agent(payload, tools)
result = await plugin.xxx_agent(payload, tools, session_id="s1")

# 2) Unified entry point (dispatch by name)
result = await plugin.run("xxx_agent", payload, tools=tools, session_id="s1")

# 3) Raw framework envelope (monitoring / troubleshooting)
envelope = await plugin.run_envelope("xxx_agent", payload, tools=tools)
```

### Return value semantics

```python
result                 # XXXOutput instance (dynamic subclass); isinstance(result, XXXOutput) == True
result.model_dump()    # only the declared fields, exactly matching the output schema
result.ok              # proxy property: True
result.finish_reason   # proxy property: stop / reset / compact ...
result.meta            # proxy property: token accounting, session info, and other metadata
result.error           # proxy property: failure reason (empty on success)
```

Metadata is kept in Pydantic private attributes, so it **never pollutes the output schema** and never appears in `model_dump()`.

Raw framework envelope (`run_envelope`):

```json
{
  "ok": true,
  "data": "<XxxOutput instance>",
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

`finish_reason` values: `stop` / `reset` (session TTL expired and was reset) / `compact` (tool-iteration budget exhausted) / `not_registered` /
`input_schema_invalid` / `output_schema_invalid` / `tool_error` / `error`.

### Sessions and compaction

Sessions live in **process memory only** (the framework never writes to disk and has no persistence layer):

- No `session_id`: every call starts a fresh session with no history.
- Same `session_id`: the engine instance is reused within the TTL and history is carried into the context automatically.
- After the TTL expires, the same `session_id` starts over (`finish_reason="reset"`) and the history is cleared.
- History does not survive a process restart; if you need that, map `session_id` to your own external storage.

Context compaction:

- When the context exceeds `max_prompt_tokens` (8000 by default) the framework calls `agent.compact()` automatically.
- Compaction first summarizes the context (one extra model call); afterwards the context is just `system + summary`.
- A failed compaction does not break the main flow; the reason is written to `meta["compact_error"]`.
- Sessions that used tools also compact correctly: before compacting, the framework converts `tool_calls` into a readable form (the engine reads dicts, but at runtime they are objects).

```python
plugin = XxxPlugin(
    model="gpt-4o-mini",
    api_key=api_key,
    compact_config={"enabled": True, "max_prompt_tokens": 8000},
    max_tool_iterations=8,   # tool-call iteration budget
    max_turns=10,            # per-call ReAct turn budget
)
```

### Error handling

Both direct calls and `run(...)` **raise** on failure; they never return empty objects:

| Exception | Situation |
| --- | --- |
| `AgentNotRegisteredError` | The agent is not registered |
| `InputSchemaError` | The input does not match the declared model |
| `OutputSchemaError` | The model output does not match the declared model |
| `ToolExecutionError` | The tool is missing or failed |
| `FrameworkError` | Other runtime errors (base class) |

```python
from zhkj_llm_function import OutputSchemaError

try:
    result = await plugin.xxx_agent(payload, tools)
except OutputSchemaError as exc:
    ...
```

## Constructor arguments

```python
plugin = XxxPlugin(
    model="gpt-4o-mini",
    api_key=os.getenv("LLM_API_KEY"),
    model_kwargs={"base_url": os.getenv("LLM_BASE_URL")},  # custom gateway / proxy
    temperature=1,
    session_store=None,        # custom session store (in-memory by default)
    compact_config=None,       # compaction policy
    max_tool_iterations=8,
    max_turns=10,
    enable_todo_write=False,
    storage=None,              # forwarded to tinyagent
    logger=None,
)
```

`set_llm()` switches model or credentials at runtime:

```python
plugin.set_llm({"model": "gpt-4o", "api_key": "sk-xxx"})
```

## Examples

The repository ships three runnable example plugins.

### Courier tracking

- Plugin: `plugins/courier_plugin.py` (`courier_trace_agent`)
- Run: `.venv\Scripts\python.exe examples\run_demo.py`
  (without `LLM_API_KEY` it uses a built-in fake model, so it runs fully offline)

### Planning tomorrow's itinerary from tomorrow's weather

- Plugin: `plugins/weather_plan_plugin.py` (`weather_plan_agent`)
- External tool: `examples/weather_tools.py` (`query_weather_forecast`, backed by Open-Meteo, free and keyless)
- Run: `.venv\Scripts\python.exe examples\run_weather_plan.py`

```python
result = await plugin.weather_plan_agent({"city": "Hangzhou"}, tools)
# result.plan_date / result.weather_summary / result.temperature_range
# result.precipitation_probability_max / result.has_heavy_rain / result.plan[] / result.suggestions[]
```

A sample of the business rules (written in the docstring, converted into a system prompt by the framework):
the weather tool must be called and values must never be invented; `plan_date` comes from `payload.date` or the day after today (the framework injects the current date into the user input);
when the precipitation probability is >= 0.5, or when there is a thunderstorm or rain/snow, that time slot becomes indoor; at >= 33 C or <= 5 C, outdoor time is reduced;
the plan is split into morning / afternoon / evening with at least three items, and every item states its weather basis and rationale.

### Short-term crypto signal from recent news and historical performance

- Plugin: `plugins/crypto_signal_plugin.py` (`crypto_signal_agent`)
- External tools: `examples/crypto_tools.py`
  - `compute_market_stats`: momentum / annualized volatility / max drawdown / RSI / relative strength (**deterministic statistics; the model never does the arithmetic**)
  - `fetch_daily_candles`: **live market data** (Binance public klines, with automatic failover between hosts)
  - `fetch_price_history`: **live market data** backup source (CoinGecko market_chart)
  - `fetch_recent_news`: **live news** (CoinDesk RSS → Cointelegraph RSS → Binance announcements, with per-source degradation, title de-duplication and BTC/Bitcoin style alias filtering)
  - All sources are public and **need no API key**; when an interface is unavailable the example falls back to built-in sample data and labels the source
- Run: `.venv\Scripts\python.exe examples\run_crypto_signal.py`
  - Environment variables: `LLM_API_KEY` (real model when set, otherwise a built-in fake model that exercises the exact same tool path), `LLM_MODEL`, `LLM_BASE_URL`,
    `CRYPTO_SYMBOL` (default `BTCUSDT`), `CRYPTO_HORIZON` (default 7 days), `CRYPTO_POSITION` (none/long/short)
  - The example prints the **data sources, the quantitative summary and the real news headlines** before the structured signal

```python
result = await plugin.crypto_signal_agent(
    {"symbol": "BTCUSDT", "horizon_days": 7, "news": news, "prices": prices},
    tools,
)
# result.signal(bullish/bearish/neutral) / result.confidence / result.expected_move_pct
# result.volatility_pct / result.key_drivers[] / result.risks[] / result.invalidation_conditions[]
```

The point: **every number comes from a tool**; the model only applies the rules to the data and organizes a structured signal.
The decision rules (momentum state x news direction → direction, confidence caps, ranges only instead of point targets, mandatory invalidation conditions) are all written in the docstring.
The output is a research signal, not investment advice.

## Repository layout

```
zhkj_llm_function/       # framework package
  __init__.py             # public exports
  agent.py                # agent_def decorator / AgentSpec / BaseAgentGroup
  runtime.py              # runtime: drives tinyagent, produces the unified result
  tools.py                # tool normalization + schema reflection + execution
  session.py              # session store (in-memory, TTL managed)
  schema.py               # Pydantic schema / docstring parsing
  protocol.py             # <|tool_call|> tagged protocol and JSON extraction
  compat.py               # tinyagent / litellm message object compatibility
  result.py               # output proxy result object
  errors.py               # exception definitions
plugins/                  # example plugins (reference implementations)
examples/                 # runnable examples
tests/                    # end-to-end tests
```

## Development and testing

```bash
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"

python -m pytest tests -q          # all cases
python -m pytest tests/test_tool_session_compact.py -v   # tool calls + session + compaction
python examples/run_demo.py        # offline end-to-end example
```

The tests never touch a real model: they fake `litellm.acompletion` with OpenAI-style responses, so CI stays stable.

## FAQ

**Q: Why are tools injected from the outside?**
Hard-coding tools inside an agent couples the plugin to a specific data source and makes it impossible to swap in tests. With injected tools the plugin only declares *what capability it needs*, the caller decides which backend to plug in, and one plugin can be reused by many businesses.

**Q: Why is the return value not `{ok, data, ...}`?**
Callers usually care about the business fields. Returning the output model instance lets you write code against the declared schema, while `ok / finish_reason / meta` remain available as proxy properties — no information is lost and the business structure stays clean. Use `run_envelope(...)` when you need the raw envelope.

**Q: What if the model does not return valid JSON?**
An `OutputSchemaError` is raised (inside `run_envelope` it becomes `finish_reason="output_schema_invalid"`). Put an explicit instruction in the business rules: "output structured JSON only, no extra explanation".

**Q: How do I use a self-hosted gateway or proxy?**
Pass it through with `model_kwargs={"base_url": ...}`, which is forwarded to litellm; the examples use the `LLM_BASE_URL` environment variable.

**Q: Do sessions grow forever?**
Sessions live in memory and are bound by a TTL, after which they reset; within a single call, a context above `max_prompt_tokens` is compacted automatically.
