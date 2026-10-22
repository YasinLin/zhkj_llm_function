"""加密货币信号插件的测试：形状、工具调用闭环与统计工具正确性。"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm  # noqa: E402

from plugins.crypto_signal_plugin import (  # noqa: E402
    CryptoSignalInput,
    CryptoSignalOutput,
    CryptoSignalPlugin,
)


class FakeLLM(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def make_response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


def make_tool_call(name, arguments, call_id="call_1"):
    function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    return SimpleNamespace(id=call_id, function=function, type="function")


@pytest.fixture(autouse=True)
def fake_litellm(monkeypatch):
    holder = {}

    def install(responses):
        fake = FakeLLM(responses)
        holder["fake"] = fake

        async def _acompletion(**kwargs):
            return await fake(**kwargs)

        monkeypatch.setattr(litellm, "acompletion", _acompletion)
        return fake

    holder["install"] = install
    return holder


def run(coro):
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------------- 用例
def test_crypto_plugin_shape():
    specs = CryptoSignalPlugin.agent_specs()
    assert "crypto_signal_agent" in specs
    spec = specs["crypto_signal_agent"]
    assert spec.ttl == 3600
    assert "compute_market_stats" in spec.business_instruction
    assert "不构成投资建议" in spec.business_instruction
    assert set(spec.input_schema["properties"]) == {
        "symbol",
        "horizon_days",
        "news",
        "prices",
        "benchmark_prices",
        "position",
        "max_confidence",
    }
    assert set(spec.output_schema["properties"]) == {
        "symbol",
        "horizon_days",
        "signal",
        "confidence",
        "expected_move_pct",
        "volatility_pct",
        "key_drivers",
        "risks",
        "invalidation_conditions",
        "summary",
        "disclaimer",
        "error_message",
    }
    assert CryptoSignalInput(symbol="BTCUSDT").horizon_days == 7


def test_compute_market_stats_is_deterministic():
    from examples.crypto_tools import compute_market_stats

    # 10 天连续上涨 1%
    prices = [100.0 * (1.01 ** i) for i in range(20)]
    stats = compute_market_stats("BTCUSDT", prices, horizon_days=7)
    assert stats["momentum_state"] == "up"
    assert stats["momentum_pct"] > 6.0
    assert stats["volatility_state"] in ("low", "mid")
    assert stats["max_drawdown_pct"] <= 0.0
    assert stats["rsi14"] > 90

    # 暴跌 -> strong_down
    falling = [100.0 * (0.95 ** i) for i in range(20)]
    down = compute_market_stats("BTCUSDT", falling, horizon_days=7)
    assert down["momentum_state"] == "strong_down"
    assert down["momentum_pct"] < -20

    # 相对强弱
    bench = [100.0 * (1.001 ** i) for i in range(20)]
    rs = compute_market_stats("BTCUSDT", prices, horizon_days=7, benchmark_prices=bench)
    assert rs["relative_strength_pct"] > 0


def test_crypto_signal_agent_end_to_end(fake_litellm):
    from examples.crypto_tools import compute_market_stats

    prices = [100.0 * (1.01 ** i) for i in range(30)]
    fake = fake_litellm["install"](
        [
            make_response(
                content="",
                tool_calls=[
                    make_tool_call(
                        "compute_market_stats",
                        {"symbol": "BTCUSDT", "prices": prices, "horizon_days": 7},
                    )
                ],
            ),
            make_response(
                content=json.dumps(
                    {
                        "symbol": "BTCUSDT",
                        "horizon_days": 7,
                        "signal": "bullish",
                        "confidence": 0.55,
                        "expected_move_pct": "-3.0% ~ +2.5%",
                        "volatility_pct": 12.4,
                        "key_drivers": [
                            {
                                "factor": "momentum",
                                "evidence": "近7日累计涨幅 7.07%",
                                "impact": "positive",
                            }
                        ],
                        "risks": ["低波动环境下易出现假突破"],
                        "invalidation_conditions": ["日收盘跌破 103.0"],
                        "summary": "动量偏多但需设好失效条件",
                        "disclaimer": "仅供研究参考，不构成投资建议",
                        "error_message": "",
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )

    def compute_market_stats_tool(
        symbol: str,
        prices: list[float],
        horizon_days: int = 7,
        benchmark_prices: list[float] | None = None,
    ) -> dict:
        return compute_market_stats(symbol, prices, horizon_days, benchmark_prices)

    tools = [
        {
            "name": "compute_market_stats",
            "description": "计算量化指标",
            "func": compute_market_stats_tool,
        }
    ]

    plugin = CryptoSignalPlugin(model="gpt-4o-mini")
    result = run(
        plugin.crypto_signal_agent(
            {
                "symbol": "BTCUSDT",
                "horizon_days": 7,
                "news": [
                    {"title": "机构买入增加", "summary": "现货买盘上升", "source": "示例", "published_at": "2026-03-01"}
                ],
                "prices": prices,
            },
            tools,
        )
    )

    assert isinstance(result, CryptoSignalOutput)
    assert result.signal == "bullish"
    assert 0.1 <= result.confidence <= 1.0
    assert result.expected_move_pct.count("%") == 2
    assert result.key_drivers[0].factor == "momentum"
    assert result.disclaimer
    assert result.ok is True

    # 工具参数 schema 自动反射
    tool_schema = [
        item["function"]
        for item in fake.calls[0]["tools"]
        if item["function"]["name"] == "compute_market_stats"
    ][0]
    assert set(tool_schema["parameters"]["properties"]) == {
        "symbol",
        "prices",
        "horizon_days",
        "benchmark_prices",
    }


def test_tool_list_includes_real_interfaces():
    from examples.crypto_tools import build_crypto_tools

    names = [tool["name"] for tool in build_crypto_tools(include_network=True)]
    assert names == [
        "compute_market_stats",
        "fetch_daily_candles",
        "fetch_price_history",
        "fetch_recent_news",
    ]
    offline_names = [tool["name"] for tool in build_crypto_tools(include_network=False)]
    assert offline_names == ["compute_market_stats"]


def test_real_market_data_interface():
    """真实行情接口（Binance）；网络不可用则跳过。"""
    from examples.crypto_tools import compute_market_stats, fetch_daily_candles

    try:
        candles = fetch_daily_candles("BTCUSDT", 30)
    except Exception as exc:  # noqa: BLE001
        pytest.skip("Binance 行情接口不可用: {0}".format(exc))

    assert len(candles) >= 20
    assert set(candles[0]) >= {"date", "open", "high", "low", "close", "volume"}
    prices = [float(c["close"]) for c in candles]
    stats = compute_market_stats("BTCUSDT", prices, horizon_days=7)
    assert stats["count"] == len(prices)
    assert stats["annualized_vol_pct"] > 0


def test_real_news_interface():
    """真实新闻接口（CoinDesk / Cointelegraph / Binance 聚合）；网络不可用则跳过。"""
    from examples.crypto_tools import fetch_recent_news

    try:
        news = fetch_recent_news("BTCUSDT", 5)
    except Exception as exc:  # noqa: BLE001
        pytest.skip("新闻接口不可用: {0}".format(exc))

    assert news
    for item in news:
        assert item["title"]
        assert item["source"]
