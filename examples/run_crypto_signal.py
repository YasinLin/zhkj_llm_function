"""示例：结合「最近新闻 + 历史涨幅」输出加密货币短期方向信号。

运行（.venv）：
    .venv\\Scripts\\python.exe examples\\run_crypto_signal.py

数据（真实接口，无需 key）：
  - 历史行情：fetch_daily_candles  → Binance 公开 K 线（备选 fetch_price_history → CoinGecko）
  - 最近新闻：fetch_recent_news   → CoinDesk RSS / Cointelegraph RSS / Binance 资讯 聚合
  接口不可用时自动降级为内置示例数据，并在输出里标注来源。

模型：
  - 设置 LLM_API_KEY（可选 LLM_MODEL / LLM_BASE_URL）后使用真实模型；
  - 未设置时使用内置假模型（同样走完整工具链路），可离线复现。

说明：输出是研究性信号，不构成投资建议。
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import litellm  # noqa: E402

from crypto_tools import (  # noqa: E402
    build_crypto_tools,
    build_sample_news,
    build_sample_prices,
    compute_market_stats,
    fetch_daily_candles,
    fetch_price_history,
    fetch_recent_news,
)
from plugins.crypto_signal_plugin import CryptoSignalPlugin  # noqa: E402
from zhkj_llm_function import ToolDef  # noqa: E402

DISCLAIMER = "本结果由模型基于公开信息与历史统计生成，仅供研究参考，不构成投资建议。"
SYMBOL = os.getenv("CRYPTO_SYMBOL", "BTCUSDT")
HORIZON = int(os.getenv("CRYPTO_HORIZON", "7"))


def load_market_data(symbol: str, horizon_days: int):
    """优先取真实数据，失败时降级为示例数据。返回 (prices, news, sources)。"""
    sources: dict[str, str] = {}

    prices: list[float] = []
    try:
        candles = fetch_daily_candles(symbol, limit=90)
        prices = [float(c["close"]) for c in candles]
        sources["prices"] = "Binance K 线（{0} 根，最新 {1}）".format(
            len(candles), candles[-1]["date"]
        )
    except Exception as exc:  # noqa: BLE001
        try:
            history = fetch_price_history(symbol, limit=90)
            prices = [float(c["close"]) for c in history]
            sources["prices"] = "CoinGecko 历史（{0} 根，最新 {1}）".format(
                len(history), history[-1]["date"]
            )
        except Exception as exc2:  # noqa: BLE001
            prices = build_sample_prices(symbol)
            sources["prices"] = "内置示例数据（Binance: {0}；CoinGecko: {1}）".format(exc, exc2)

    news: list[dict] = []
    try:
        news = fetch_recent_news(symbol, limit=8)
        sources["news"] = "CoinDesk / Cointelegraph / Binance 聚合（{0} 条）".format(len(news))
    except Exception as exc:  # noqa: BLE001
        news = build_sample_news(symbol)
        sources["news"] = "内置示例新闻（接口不可用: {0}）".format(exc)

    return prices, news, sources


def install_fake_llm(stats: dict, prices: list[float]) -> None:
    """假模型：先调用统计工具，再输出结构化信号。"""

    momentum = stats["momentum_pct"]
    state = stats["momentum_state"]
    bullish = state in ("up", "strong_up")
    bearish = state in ("down", "strong_down")
    signal = "bullish" if bullish else ("bearish" if bearish else "neutral")
    volatility = stats["annualized_vol_pct"]
    half = volatility / 8.0
    if signal == "neutral":
        confidence = 0.45
    else:
        confidence = 0.55 if volatility < 80 else 0.42

    payload = {
        "symbol": stats["symbol"],
        "horizon_days": stats["horizon_days"],
        "signal": signal,
        "confidence": confidence,
        "expected_move_pct": "-{0:.1f}% ~ +{1:.1f}%".format(half, half * 0.8),
        "volatility_pct": volatility,
        "key_drivers": [
            {
                "factor": "momentum",
                "evidence": "近{0}日累计涨幅 {1:.2f}%，动量状态 {2}".format(
                    stats["horizon_days"], momentum, state
                ),
                "impact": "positive" if bullish else "negative" if bearish else "neutral",
            },
            {
                "factor": "volatility",
                "evidence": "年化波动率 {0:.2f}%，波动状态 {1}，区间最大回撤 {2:.2f}%".format(
                    volatility, stats["volatility_state"], stats["max_drawdown_pct"]
                ),
                "impact": "neutral",
            },
            {
                "factor": "news",
                "evidence": "近期新闻与动量方向一致，消息面未见强反转信号",
                "impact": "positive" if bullish else "negative" if bearish else "neutral",
            },
        ],
        "risks": [
            "年化波动率处于 {0}，价格波动可能显著放大".format(stats["volatility_state"]),
            "单条快讯的反转风险高，消息面存在不确定性",
            "区间上沿 {0:.2f} / 下沿 {1:.2f}，突破或跌破均属常态".format(
                stats["price_max"], stats["price_min"]
            ),
        ],
        "invalidation_conditions": [
            "日收盘跌破近{0}日区间下沿 {1:.2f}".format(stats["horizon_days"], stats["price_min"]),
            "日涨幅连续两日低于 -3%，动量状态转为 down",
            "出现监管处罚或交易规则实质性收紧的公告",
        ],
        "summary": "动量{0}，方向偏{1}，但需设好失效条件并控制仓位".format(
            state, "多" if bullish else "空" if bearish else "中性"
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
                                        {
                                            "symbol": SYMBOL,
                                            "prices": prices,
                                            "horizon_days": HORIZON,
                                        }
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
    state_holder = {"index": 0}

    async def _acompletion(**kwargs):
        index = min(state_holder["index"], len(responses) - 1)
        state_holder["index"] += 1
        return responses[index]

    litellm.acompletion = _acompletion


async def main() -> None:
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    base_url = os.getenv("LLM_BASE_URL")

    prices, news, sources = load_market_data(SYMBOL, HORIZON)
    stats = compute_market_stats(SYMBOL, prices, horizon_days=HORIZON)

    print("[数据来源]")
    for key, value in sources.items():
        print("  - {0}: {1}".format(key, value))
    print(
        "[量化概览] {0} 近{1}日 {2:+.2f}% | 动量 {3} | 年化波动 {4:.1f}% ({5}) | 回撤 {6:.2f}% | RSI14 {7}".format(
            SYMBOL,
            HORIZON,
            stats["momentum_pct"],
            stats["momentum_state"],
            stats["annualized_vol_pct"],
            stats["volatility_state"],
            stats["max_drawdown_pct"],
            stats["rsi14"],
        )
    )
    print("[最近新闻]")
    for item in news[:5]:
        print("  - [{0}] {1}".format(item.get("source", ""), item.get("title", "")))

    tools: list[ToolDef] = build_crypto_tools(include_network=True)
    if api_key:
        model_kwargs = {"base_url": base_url} if base_url else None
    else:
        print("[模型] 未检测到 LLM_API_KEY，使用内置假模型（工具链路与真实模式一致）")
        install_fake_llm(stats, prices)
        model_kwargs = None

    plugin = CryptoSignalPlugin(
        model=model, temperature=1, api_key=api_key, model_kwargs=model_kwargs
    )
    result = await plugin.crypto_signal_agent(
        {
            "symbol": SYMBOL,
            "horizon_days": HORIZON,
            "news": [
                {
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "source": item.get("source", ""),
                    "published_at": item.get("published_at", ""),
                    "url": item.get("url", ""),
                }
                for item in news
            ],
            "prices": prices,
            "position": os.getenv("CRYPTO_POSITION", "none"),
        },
        tools,
    )

    print("[信号输出]")
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2, default=str))
    print("  ok={0} finish_reason={1}".format(result.ok, result.finish_reason))
    print("  meta={0}".format(json.dumps(result.meta, ensure_ascii=False, default=str)))
    print("\n[提醒] " + DISCLAIMER)


if __name__ == "__main__":
    asyncio.run(main())
