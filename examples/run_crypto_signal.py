"""示例：结合「最近新闻 + 历史涨幅」输出加密货币短期方向信号。

运行（.venv）：
    .venv\\Scripts\\python.exe examples\\run_crypto_signal.py

- 真实模式：设置 LLM_API_KEY（可选 LLM_MODEL / LLM_BASE_URL）后运行；
  联网取数可用 tools 中的 fetch_daily_candles / fetch_recent_news。
- 离线模式：未设置 LLM_API_KEY 时使用内置假模型 + 确定性示例行情，可离线跑通全链路。

说明：输出是研究性信号，不构成投资建议。
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm  # noqa: E402

from plugins.crypto_signal_plugin import CryptoSignalPlugin  # noqa: E402
from zhkj_llm_function import ToolDef  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crypto_tools import (  # noqa: E402
    build_crypto_tools,
    build_sample_news,
    build_sample_prices,
    compute_market_stats,
)

DISCLAIMER = "本结果由模型基于公开信息与历史统计生成，仅供研究参考，不构成投资建议。"


def install_fake_llm(stats: dict) -> None:
    """假模型：先调用统计工具，再输出结构化信号。"""

    momentum = stats["momentum_pct"]
    state = stats["momentum_state"]
    bullish = state in ("up", "strong_up")
    signal = "bullish" if bullish else ("bearish" if state in ("down", "strong_down") else "neutral")
    volatility = stats["annualized_vol_pct"]

    payload = {
        "symbol": stats["symbol"],
        "horizon_days": stats["horizon_days"],
        "signal": signal,
        "confidence": 0.55 if volatility < 80 else 0.42,
        "expected_move_pct": "-{0:.1f}% ~ +{1:.1f}%".format(volatility / 10, volatility / 12),
        "volatility_pct": volatility,
        "key_drivers": [
            {
                "factor": "momentum",
                "evidence": "近{0}日累计涨幅 {1:.2f}%，动量状态 {2}".format(
                    stats["horizon_days"], momentum, state
                ),
                "impact": "positive" if bullish else "negative",
            },
            {
                "factor": "volatility",
                "evidence": "年化波动率 {0:.2f}%，波动状态 {1}，最大回撤 {2:.2f}%".format(
                    volatility, stats["volatility_state"], stats["max_drawdown_pct"]
                ),
                "impact": "neutral",
            },
            {
                "factor": "news",
                "evidence": "新闻「机构资金持续流入现货渠道」与「波动率抬升」指向资金面偏正、但分歧加大",
                "impact": "positive" if bullish else "neutral",
            },
        ],
        "risks": [
            "年化波动率处于 {0}，价格波动可能显著放大".format(stats["volatility_state"]),
            "期权隐含波动率抬升，市场分歧加大，方向易反复",
            "消息面存在不确定性，单条快讯的反转风险高",
        ],
        "invalidation_conditions": [
            "日收盘跌破近{0}日区间下沿 {1:.2f}".format(stats["horizon_days"], stats["price_min"]),
            "出现监管处罚或交易规则实质性收紧的公告",
            "日涨幅连续两日低于 -3%，动量状态转为 down",
        ],
        "summary": "动量{0}但波动抬升，方向偏{1}，建议轻仓并设好失效条件".format(
            state, "多" if bullish else "空" if signal == "bearish" else "中性"
        ),
        "disclaimer": DISCLAIMER,
        "error_message": "",
    }

    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                type="function",
                                function=SimpleNamespace(
                                    name="compute_market_stats",
                                    arguments=json.dumps(
                                        {"symbol": "BTCUSDT", "prices": build_sample_prices(),
                                         "horizon_days": 7}
                                    ),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(payload, ensure_ascii=False), tool_calls=[]
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        ),
    ]
    state = {"index": 0}

    async def _acompletion(**kwargs):
        index = min(state["index"], len(responses) - 1)
        state["index"] += 1
        return responses[index]

    litellm.acompletion = _acompletion


async def main() -> None:
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    base_url = os.getenv("LLM_BASE_URL")

    prices = build_sample_prices()
    news = build_sample_news()
    stats = compute_market_stats("BTCUSDT", prices, horizon_days=7)

    if api_key:
        tools: list[ToolDef] = build_crypto_tools(include_network=True)
        model_kwargs = {"base_url": base_url} if base_url else None
    else:
        print("[demo] 未检测到 LLM_API_KEY，使用内置假模型 + 确定性示例行情离线演示")
        install_fake_llm(stats)
        tools = [
            {
                "name": "compute_market_stats",
                "description": "计算交易对的历史动量、波动率、回撤、RSI、相对强弱等量化指标",
                "func": compute_market_stats,
            }
        ]
        model_kwargs = None

    plugin = CryptoSignalPlugin(model=model, temperature=1, api_key=api_key, model_kwargs=model_kwargs)
    result = await plugin.crypto_signal_agent(
        {
            "symbol": "BTCUSDT",
            "horizon_days": 7,
            "news": news,
            "prices": prices,
            "position": "none",
        },
        tools,
    )

    print(json.dumps({
        "data": result.model_dump(),
        "ok": result.ok,
        "finish_reason": result.finish_reason,
        "meta": result.meta,
    }, ensure_ascii=False, indent=2, default=str))
    print("\n[提醒] " + DISCLAIMER)


if __name__ == "__main__":
    asyncio.run(main())
