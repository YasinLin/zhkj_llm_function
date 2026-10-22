"""示例插件：结合「最近新闻 + 历史涨幅」给出加密货币短期方向信号。

设计原则（与框架一致）：
- 量化指标（动量、波动率、回撤、RSI、漂移）一律由外部工具计算，模型不碰算术；
- 判断规则写进 docstring，框架自动转 system prompt；
- 输出是强类型模型（方向、置信度、预期波动区间、驱动因子、风险、失效条件）；
- 结论是研究性信号，不构成投资建议（输出里强制带免责声明）。
"""

from pydantic import BaseModel, Field

from zhkj_llm_function import BaseAgentGroup, ToolDef, agent_def


class NewsItem(BaseModel):
    """单条新闻"""

    title: str = Field(..., description="新闻标题")
    summary: str = Field(default="", description="新闻摘要原文")
    source: str = Field(default="", description="来源媒体")
    published_at: str = Field(default="", description="发布时间，ISO 字符串")
    url: str = Field(default="", description="原文链接")


class CryptoSignalInput(BaseModel):
    """加密货币短期信号入参"""

    symbol: str = Field(..., description="交易对，例如 BTCUSDT")
    horizon_days: int = Field(default=7, ge=1, le=90, description="预测周期（天）")
    news: list[NewsItem] = Field(default_factory=list, description="最近相关新闻（时间倒序或正序均可）")
    prices: list[float] = Field(
        default_factory=list, description="按时间升序的日收盘价列表（至少 15 个，越多越好）"
    )
    benchmark_prices: list[float] = Field(
        default_factory=list, description="基准标的（如 BTC）同周期日收盘价，用于计算相对强弱"
    )
    position: str = Field(
        default="none", description="当前持仓：none(空仓) / long(多头) / short(空仓)"
    )
    max_confidence: float = Field(
        default=0.8, ge=0.1, le=1.0, description="允许输出的最高置信度（风险厌恶度）"
    )


class SignalDriver(BaseModel):
    """驱动因子"""

    factor: str = Field(..., description="因子类型：news / momentum / volatility / relative_strength / flow")
    evidence: str = Field(..., description="依据，必须引用具体数值或具体新闻标题")
    impact: str = Field(..., description="影响方向：positive / negative / neutral")


class CryptoSignalOutput(BaseModel):
    """加密货币短期信号出参"""

    symbol: str = Field(..., description="回显的交易对")
    horizon_days: int = Field(..., description="预测周期（天）")
    signal: str = Field(..., description="方向：bullish / bearish / neutral")
    confidence: float = Field(..., description="置信度 0~1")
    expected_move_pct: str = Field(..., description="预期波动区间，如 -4% ~ +3%，只给区间不给点目标")
    volatility_pct: float = Field(default=0.0, description="年化波动率（%），来自工具计算")
    key_drivers: list[SignalDriver] = Field(default_factory=list, description="关键驱动因子，2~5 条")
    risks: list[str] = Field(default_factory=list, description="主要风险，2~4 条")
    invalidation_conditions: list[str] = Field(
        default_factory=list, description="失效条件（触发即视为判断错误），2~4 条"
    )
    summary: str = Field(default="", description="一句话结论，不超过 60 字")
    disclaimer: str = Field(default="", description="免责声明，必须保留")
    error_message: str = Field(default="", description="数据不足或工具失败时的原因说明，成功时为空")


class CryptoSignalPlugin(BaseAgentGroup):
    """基于新闻与历史涨幅的加密货币短期方向信号插件。"""

    @agent_def(ttl=3600, compact_config=None)
    async def crypto_signal_agent(
        self, payload: CryptoSignalInput, tools: list[ToolDef] | None = None
    ) -> CryptoSignalOutput:
        """
        角色：加密货币短期信号分析师，基于「最近新闻 + 历史涨幅」给出未来若干天的方向性判断与风险提示。

        业务规则：
        1. 数据纪律：
           1) 所有数值指标（动量、波动率、回撤、RSI、相对强弱）必须来自工具 compute_market_stats 的返回值，禁止自己计算或臆造数字；
           2) 调用 compute_market_stats 时必须传入 prices：优先用 payload.prices；若 payload.prices 为空，先调用 fetch_daily_candles（失败时用 fetch_price_history）取历史日线，把 close 序列作为 prices 传入；
           3) 若最终可用价格数据点不足 15 个，视为历史数据不足：仍需调用工具确认可用字段，然后 signal=neutral、confidence≤0.3、key_drivers 为空、expected_move_pct 填 "无法评估"，并在 error_message 写明「历史价格数据不足」；
           4) 若 payload.news 为空，必须先调用 fetch_recent_news 获取新闻再判断；工具不可用或失败时，signal=neutral，并在 error_message 写明「缺少新闻输入且无法获取新闻」。
        2. 新闻处理：
           1) 只使用 payload.news 与工具返回的新闻，禁止引入未提供的新闻、传闻或「市场普遍认为」；
           2) 每条被采用的新闻必须在 key_drivers 中引用其标题或摘要要点，不得写「情绪偏暖」这类无出处结论；
           3) 新闻与价格信号冲突时，evidence 中同时写明两者方向，并在 summary 说明以哪一侧为主及原因。
        3. 信号判定规则（按顺序判定，命中即止）：
           1) 动量状态为 strong_up 且无重大负面新闻 → bullish；
           2) 动量状态为 strong_down 且无重大正面新闻 → bearish；
           3) 动量状态为 up 且新闻面偏正面 → bullish；
           4) 动量状态为 down 且新闻面偏负面 → bearish；
           5) 其余情况（动量中性、波动率高、新闻与价格背离、证据不足）→ neutral；
           6) 动量状态与波动状态均取工具返回的 momentum_state / volatility_state 字段。
        4. 置信度：
           1) confidence 必须落在 0.1~payload.max_confidence 之间；
           2) 证据不足、新闻与价格背离、volatility_state=high 时，confidence 不得超过 0.5；
           3) neutral 时 confidence 不得超过 0.5。
        5. 预期区间：
           1) expected_move_pct 只能给区间，格式 "-x% ~ +y%"，两端取自工具返回的波动率与动量，禁止给单点目标价或具体价格；
           2) volatility_pct 必须原样填工具返回的年化波动率数值。
        6. 风险与失效条件：
           1) risks 写 2~4 条，必须与本次数据的真实风险一致（如高波动、消息面不确定、流动性、事件风险）；
           2) invalidation_conditions 写 2~4 条可观测、可判定的条件（例如「日收盘跌破 X」「出现某类消息」），禁止写「行情不及预期」这类无法判定的话；
           3) position=long 时必须给出下行风险与对应的失效条件；position=short 时必须给出上行风险与对应的失效条件。
        7. 输出纪律：
           1) disclaimer 必须非空且包含「不构成投资建议」字样；
           2) summary 为一句话中文结论，不超过 60 字；
           3) 最终输出必须严格符合 CryptoSignalOutput 结构，仅输出结构化字段，禁止附加任何额外自然语言、Markdown 或致歉语。
        """
        ...
