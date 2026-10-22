"""加密货币信号分析用的外部工具（调用时通过 tools= 传入，插件内不定义工具）。

- compute_market_stats：确定性统计，动量/波动率/回撤/RSI/相对强弱，**不让模型做算术**
- fetch_daily_candles：可选，Binance 公开 K 线接口（无需 key，网络不通时抛错）
- fetch_recent_news：可选，CoinDesk RSS（网络不通时抛错）
"""

import json
import math
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------- 确定性统计


def _returns(prices: List[float]) -> List[float]:
    """日收益率序列。"""
    return [
        (prices[i] / prices[i - 1] - 1.0) for i in range(1, len(prices)) if prices[i - 1] > 0
    ]


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _momentum_state(momentum_pct: float) -> str:
    if momentum_pct >= 10:
        return "strong_up"
    if momentum_pct >= 2:
        return "up"
    if momentum_pct <= -10:
        return "strong_down"
    if momentum_pct <= -2:
        return "down"
    return "flat"


def _volatility_state(annualized_pct: float) -> str:
    if annualized_pct < 40:
        return "low"
    if annualized_pct < 80:
        return "mid"
    return "high"


def _rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 0.0
    gains, losses = [], []
    for i in range(len(prices) - period, len(prices)):
        delta = prices[i] - prices[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _max_drawdown_pct(prices: List[float]) -> float:
    peak = prices[0] if prices else 0.0
    worst = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak > 0:
            worst = min(worst, (price / peak - 1.0) * 100.0)
    return worst


def _drift_pct_per_day(prices: List[float]) -> float:
    """对数价格线性回归斜率（日漂移 %）。"""
    if len(prices) < 3:
        return 0.0
    logs = [math.log(p) for p in prices if p > 0]
    n = len(logs)
    if n < 3:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(logs) / n
    num = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(logs))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return (num / den) * 100.0


def compute_market_stats(
    symbol: str,
    prices: List[float],
    horizon_days: int = 7,
    benchmark_prices: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """计算标的的动量、波动率、回撤、RSI 与相对强弱等量化指标。

    参数：
    - symbol: 交易对，例如 BTCUSDT
    - prices: 按时间升序的日收盘价
    - horizon_days: 计算动量的周期（天）
    - benchmark_prices: 基准标的同期日收盘价，可为空
    """
    if not prices:
        raise ValueError("prices 不能为空")

    horizon = max(1, int(horizon_days))
    window = prices[-horizon - 1 :] if len(prices) > horizon else list(prices)
    rets = _returns(prices)
    window_rets = _returns(window)

    last = prices[-1]
    base = prices[-horizon - 1] if len(prices) > horizon else prices[0]
    momentum_pct = (last / base - 1.0) * 100.0 if base > 0 else 0.0

    daily_vol = _stdev(rets)
    annualized_pct = daily_vol * math.sqrt(365) * 100.0

    relative_strength_pct = None
    if benchmark_prices and len(benchmark_prices) >= 2:
        bench_base = (
            benchmark_prices[-horizon - 1]
            if len(benchmark_prices) > horizon
            else benchmark_prices[0]
        )
        bench_last = benchmark_prices[-1]
        if bench_base > 0:
            bench_move = (bench_last / bench_base - 1.0) * 100.0
            relative_strength_pct = round(momentum_pct - bench_move, 2)

    return {
        "symbol": symbol,
        "count": len(prices),
        "window_days": len(window),
        "horizon_days": horizon,
        "price_last": last,
        "price_min": min(window),
        "price_max": max(window),
        "momentum_pct": round(momentum_pct, 2),
        "momentum_state": _momentum_state(momentum_pct),
        "daily_vol_pct": round(daily_vol * 100.0, 3),
        "annualized_vol_pct": round(annualized_pct, 2),
        "volatility_state": _volatility_state(annualized_pct),
        "max_drawdown_pct": round(_max_drawdown_pct(window), 2),
        "rsi14": round(_rsi(prices), 2),
        "drift_pct_per_day": round(_drift_pct_per_day(window), 4),
        "avg_abs_move_pct": round(
            sum(abs(r) for r in window_rets) / len(window_rets) * 100.0 if window_rets else 0.0,
            3,
        ),
        "relative_strength_pct": relative_strength_pct,
        "recent_returns_pct": [round(r * 100.0, 3) for r in rets[-10:]],
        "note": "所有数值均为历史统计，不代表未来收益",
    }


# --------------------------------------------------------------- 可选：联网取数


def fetch_daily_candles(symbol: str, limit: int = 90) -> List[Dict[str, Any]]:
    """拉取日线收盘价（Binance 公开接口，无需 key）。

    参数：
    - symbol: 交易对，例如 BTCUSDT
    - limit: 拉取根数
    """
    query = urllib.parse.urlencode(
        {"symbol": symbol.upper(), "interval": "1d", "limit": max(20, min(int(limit), 500))}
    )
    url = "https://api.binance.com/api/v3/klines?" + query
    request = urllib.request.Request(url, headers={"User-Agent": "zhkj-llm-function/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        rows = json.loads(response.read().decode("utf-8"))

    candles = []
    for row in rows:
        candles.append(
            {
                "date": str(date.fromtimestamp(int(row[0]) / 1000)),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    return candles


def fetch_recent_news(symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
    """拉取最近相关新闻（CoinDesk RSS，无需 key）。

    参数：
    - symbol: 交易对，例如 BTCUSDT（用于过滤标题）
    - limit: 返回条数
    """
    url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    request = urllib.request.Request(url, headers={"User-Agent": "zhkj-llm-function/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8", errors="replace")

    import xml.etree.ElementTree as ET

    root = ET.fromstring(raw)
    keyword = symbol.replace("USDT", "").upper()
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        if keyword and keyword not in title.upper() and keyword not in description.upper():
            continue
        items.append(
            {
                "title": title,
                "summary": description,
                "source": "CoinDesk",
                "published_at": published,
                "url": link,
            }
        )
        if len(items) >= max(1, int(limit)):
            break
    return items


def build_crypto_tools(include_network: bool = True) -> List[Dict[str, Any]]:
    """构造工具列表（参数 schema 由框架自动反射）。"""
    tools: List[Dict[str, Any]] = [
        {
            "name": "compute_market_stats",
            "description": "计算交易对的历史动量、波动率、回撤、RSI、相对强弱等量化指标",
            "func": compute_market_stats,
        }
    ]
    if include_network:
        tools.append(
            {
                "name": "fetch_daily_candles",
                "description": "拉取交易对最近的日线收盘价（Binance 公开接口）",
                "func": fetch_daily_candles,
            }
        )
        tools.append(
            {
                "name": "fetch_recent_news",
                "description": "拉取交易对最近的相关新闻（CoinDesk RSS）",
                "func": fetch_recent_news,
            }
        )
    return tools


def build_sample_prices(symbol: str = "BTCUSDT", days: int = 60) -> List[float]:
    """构造一段确定性的示例价格序列（离线演示用，非真实行情）。"""
    import random

    random.seed(20260301)
    price = 60000.0
    prices = []
    for _ in range(days):
        price *= 1.0 + random.gauss(0.002, 0.018)
        prices.append(round(price, 2))
    return prices


def build_sample_news(symbol: str = "BTCUSDT") -> List[Dict[str, Any]]:
    """构造几条示例新闻（离线演示用）。"""
    today = date.today()
    return [
        {
            "title": "机构资金持续流入现货渠道，链上持仓量创阶段新高",
            "summary": "多家做市商报告现货买盘增加，交易所余额降至近三年低位。",
            "source": "示例快讯",
            "published_at": (today - timedelta(days=1)).isoformat(),
            "url": "",
        },
        {
            "title": "监管表态：现行框架下继续推进合规托管框架",
            "summary": "监管方称将在既有框架内完善托管与披露要求，短期不改变市场规则。",
            "source": "示例快讯",
            "published_at": (today - timedelta(days=2)).isoformat(),
            "url": "",
        },
        {
            "title": "波动率抬升，期权隐含波动率升至年内高位",
            "summary": "大额期权在近月形成阻力位，市场分歧明显加大。",
            "source": "示例快讯",
            "published_at": (today - timedelta(days=3)).isoformat(),
            "url": "",
        },
    ]
