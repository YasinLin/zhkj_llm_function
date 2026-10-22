# zhkj_llm_function · Project Introduction (English)

> English version. The [中文版项目介绍](INTRO.md) covers the same ground in Chinese; usage details live in [README.md](README.md) / [README_CN.md](README_CN.md), and the scenario guide in [USE_CASES.md](USE_CASES.md).

## What it is

`zhkj_llm_function` is a declarative agent plugin framework built on [tinyagent-py](https://pypi.org/project/tinyagent-py/). On the business side you decorate an async method with `@agent_def`, declare the input and output with Pydantic models, and put the business rules in the docstring — the method body is just `...`. At call time the caller passes the tools; the framework reflects their parameter schemas, drives the engine, validates the output, and hands back the **output model instance**: `model_dump()` matches the declared output schema exactly, and framework metadata (`ok / finish_reason / meta`) is exposed through proxy properties without polluting the business structure.

The framework folds the repetitive glue code of LLM integration (prompt assembly, parameter validation, JSON parsing, error handling, session history, context trimming) into one declarative convention: tools are injected so plugins stay decoupled from data sources and reusable; typed exceptions make failures catchable by category; sessions reuse the engine instance per `session_id` and reset on TTL; the context is compacted automatically past a threshold and the compaction is observable. It fits backend services where *many businesses share one set of agent capabilities and only the underlying data source changes*.

## One-liner

**A declarative agent plugin framework on top of tinyagent-py**: declare capabilities with `@agent_def`, reflect input/output from type annotations, inject tools from the caller, and get the **output model instance** back. Ships with a **crypto short-term analysis & forecasting** example (live market data + news sentiment → directional signal, volatility, risks and invalidation conditions), and fits general **predictive analysis, news/market monitoring, report generation and knowledge-QA** workloads.

```python
result = await plugin.courier_trace_agent({"waybill_no": "SF1234567890"}, tools)
result.current_status      # "delivered"
result.ok                  # True (proxy property, does not pollute the output structure)
```

## The problems we solve

Integrating LLMs into a product repeatedly surfaces the same engineering problems:

| Pain point | Status quo | What the framework does |
| --- | --- | --- |
| Every agent rewrites the same glue | Prompt assembly, validation, JSON parsing and error handling are re-implemented each time | `@agent_def` declaration plus type-annotation reflection; the method body is just `...` |
| Agent code is coupled to a data source | Swapping a courier or weather API means editing the agent | Tools are **injected**; the plugin only declares *which capability it needs* |
| Inconsistent return shapes | Some return dicts, some tuples, framework fields mixed into business fields | The return value *is* the output model instance; `ok / finish_reason / meta` live in proxy properties |
| Vague failure handling | A malformed model output yields a half-built object | Typed exceptions: `InputSchemaError` / `OutputSchemaError` / `ToolExecutionError` |
| Sessions and context out of control | History is either lost or grows until the context explodes | The same `session_id` carries history automatically; the context is compacted past a threshold, and the compaction is observable |

## Core capabilities

- **Declarative plugins**: a capability is one `@agent_def` async method; input/output are Pydantic models; business rules are the docstring (converted into a system prompt).
- **Automatic schema reflection**: the `func` signature is the single source of truth for the parameter JSON Schema, so neither the plugin nor the caller writes schemas by hand.
- **Two-protocol compatibility**: native tinyagent function calling first, with a fallback to the framework's tagged protocol `<|tool_call|>{...}<|end_tool_call|>`.
- **Clean return values**: `isinstance(result, XXXOutput) == True`, `model_dump()` matches the declared output exactly, and the extra metadata stays in private attributes.
- **Sessions and compaction**: in-process sessions reuse the engine instance per TTL; the context is compacted automatically and a failure reason is written to `meta["compact_error"]`.
- **Testability**: no real model required — fake `litellm.acompletion` and the whole path runs end to end, so CI stays stable.
- **Built for analysis & forecasting**: standardizes "fetch → compute metrics → judge direction → emit a structured conclusion"; the model only applies the rules, and every number is produced by a tool (the crypto short-term signal is exactly this pattern).

## Architecture

```
┌──────────────────────── Caller (business code) ────────────────────┐
│  tools=[{"name": ..., "func": ...}]     payload (dict or model)     │
└───────────────┬──────────────────────────────────┬────────────────┘
                │                                  │
        ┌───────▼────────┐                 ┌───────▼────────┐
        │  Plugin layer   │                 │  Tool layer     │
        │ @agent_def      │                 │ external funcs  │
        │ Input/Output   │                 │ schema reflected │
        └───────┬────────┘                 └─────────────────┘
                │
        ┌───────▼──────────────────────────────────────────┐
        │  Framework layer: zhkj_llm_function             │
        │  runtime  execution + validation + compaction   │
        │  result   output proxy (ok/finish_reason/meta)  │
        │  session  in-memory sessions + TTL              │
        │  protocol native tool calling + tagged fallback  │
        └───────┬──────────────────────────────────────────┘
                │
        ┌───────▼────────┐
        │  tinyagent-py  │  ReAct loop / tool execution / compaction / token accounting
        └───────┬────────┘
                │
          litellm → OpenAI-compatible gateway
```

## Quick start

```python
# 1) Define the plugin
class CourierAgentPlugin(BaseAgentGroup):
    @agent_def(ttl=86400, compact_config=None)
    async def courier_trace_agent(
        self, payload: CourierTraceInput, tools: list[ToolDef] | None = None
    ) -> CourierTraceOutput:
        """
        Role: courier logistics tracking assistant.
        Business rules:
        1. You must call the tool to query; never output a logistics status from memory or guesswork.
        2. If the tool call fails, keep echoing the waybill number, leave the remaining fields empty, and write the reason into error_message.
        """
        ...

# 2) Pass the tools (the function signature is the schema)
tools = [{"name": "query_courier_waybill",
          "description": "Query the waybill status and tracking history",
          "func": query_courier_waybill}]

# 3) Call it
plugin = CourierAgentPlugin(model="gpt-4o-mini", api_key=os.getenv("LLM_API_KEY"))
result = await plugin.courier_trace_agent({"waybill_no": "SF1234567890"}, tools)
```

## How it compares to common approaches

| Dimension | Common approach | This framework |
| --- | --- | --- |
| Capability definition | Functions scattered around, each assembling prompts | One typed async method |
| Input / output | Manual validation + manual JSON parsing | Pydantic models; the framework validates and parses |
| Tools | Hard-coded inside the agent | Supplied by the caller at runtime; schemas reflected automatically |
| Return value | Business and framework fields mixed in one dict | Output instance + proxy properties (`model_dump` stays clean) |
| Failure | Returns `{"error": ...}` or raises a bare exception | Semantic exception types, catchable by category |
| Sessions | History maintained by hand | `session_id` reuse and reset out of the box |
| Context | Manual trimming | Automatic compaction past a threshold, with observable results |

## Engineering status

- Roughly 1150 lines of framework code (10 modules) and about 500 lines of tests / 29 cases, all runnable offline.
- Only two dependencies: `tinyagent-py` and `pydantic`; Python >= 3.10.
- Bundled examples: courier tracking, "plan tomorrow's itinerary from tomorrow's weather" (free Open-Meteo API), and a short-term crypto signal built from recent news plus historical performance (quantitative metrics computed by tools, live market and news interfaces included).

## Where it fits

- Several businesses need to **share one set of agent capabilities** and only swap the underlying data source (courier, weather, orders, inventory...).
- You need a **strongly typed return value** that upstream code can consume directly.
- You need to **embed LLM capabilities into an existing async service** and require classifiable, observable errors.

## Use cases and keywords

The framework is domain-agnostic: any task shaped as "**fetch data → apply rules → emit a structured conclusion**" fits. Common landing areas:

| Area | Typical task | Keywords |
| --- | --- | --- |
| **Analysis & forecasting** | Combine history with external context to judge a trend, output a direction and a range, and state the invalidation conditions | predictive analysis, trend forecasting, signal generation, directional call, time-series forecasting, prediction range, invalidation conditions, confidence calibration |
| **Crypto & digital assets** | Produce a short-term directional signal from candles and news, with volatility and risk notes | crypto, cryptocurrency, bitcoin, BTC, ETH, altcoins, digital assets, crypto price prediction, crypto signal, crypto market analysis, on-chain data, volatility, RSI, momentum, quantitative metrics |
| **Financial markets** | Summarize quotes, attribute unusual moves, flag portfolio risk | stock analysis, market commentary, price-move attribution, investment research assistant, risk alerts, portfolio monitoring |
| **News & monitoring** | Crawl, de-duplicate, classify sentiment and topics, aggregate anomaly signals | news crawling, news aggregation, sentiment analysis, text classification, information extraction, topic clustering, alerting rules, monitoring and alerting |
| **Data & reporting** | Turn heterogeneous sources into structured reports, summaries and dashboard copy | data analysis, report generation, auto summary, data insight, metric explanation, visualization copy |
| **Knowledge & retrieval** | Knowledge-base Q&A, retrieval-augmented answers with provenance | knowledge base QA, RAG, retrieval augmented generation, document QA, answer grounding, customer support |
| **Business automation** | Multi-step fetch → decide → act → acknowledge across systems | workflow orchestration, tool-calling automation, ticket handling, order processing, reconciliation, pre-approval review |
| **Content & documents** | Extract structured fields, draft content, translate and rewrite, proofread | document extraction, information extraction, summarization, content generation, translation and rewriting, proofreading, compliance check |
| **Conversational products** | Multi-turn sessions, context carry-over, intent routing | multi-turn dialogue, context management, session memory, intent detection, conversational API |
| **Engineering & evaluation** | Offline agent regression tests, output validation, metric collection | llm testing, regression testing, offline evaluation, structured output validation, prompt regression, CI integration, token accounting |

Search keywords: `agent framework`, `declarative agent`, `llm agent`, `agent plugin`, `tool calling`, `function calling`,
`structured output`, `typed result`, `pydantic schema reflection`, `multi-turn session`, `context compaction`,
`llm testing`, `predictive analysis`, `time series forecasting`, `crypto price prediction`, `crypto signal`,
`news sentiment analysis`, `market data analysis`, `anomaly detection`, `report generation`, `rag chatbot`,
`workflow automation`.

## Roadmap (planned)

- Optional external session state (Redis / database); the framework keeps the interface without forcing an implementation.
- Multiple structured-output schema candidates with automatic retry (feed the validation error back once).
- Tool concurrency switch, tool-call budgets and rate limiting.
- Observability: call-chain tracing and token/latency metric export.

## Contributing

```bash
pip install -e ".[dev]"
python -m pytest tests -q
python examples/run_demo.py          # offline end-to-end example
```

Issues and pull requests are welcome — especially new example plugins and adapters for real data sources.
