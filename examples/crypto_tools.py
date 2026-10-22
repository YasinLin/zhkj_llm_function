"""加密货币信号分析用的外部工具（调用时通过 tools= 传入，插件内不定义工具）。

- compute_market_stats：确定性统计，动量/波动率/回撤/RSI/相对强弱，**不让模型做算术**
- fetch_daily_candles：历史日线（真实行情），Binance 公开接口，多源自动容错
- fetch_price_history：历史日线（真实行情），CoinGecko 公开接口，作为备选源
- fetch_recent_news：最近新闻（真实接口），Binance 资讯 + CoinDesk RSS + Cointelegraph RSS 聚合

所有联网工具失败时抛异常，由框架包装成 ToolExecutionError，插件规则会据此降级为 neutral。
"""

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

USER_AGENT = "zhkj-llm-function/1.0"
REQUEST_TIMEOUT = 12.0

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


# --------------------------------------------------------------- 联网取数（真实接口）

BINANCE_HOSTS = ("https://api.binance.com", "https://api.binance.us", "https://api1.binance.com")

# 常见交易对 -> CoinGecko coin id
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "LTC": "litecoin",
    "TON": "the-open-network",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "TRX": "tron",
    "LINK": "chainlink",
    "BCH": "bitcoin-cash",
}


def _get(url: str, timeout: float = REQUEST_TIMEOUT) -> bytes:
    """GET 请求。"""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _base_asset(symbol: str) -> str:
    """BTCUSDT -> BTC；SOLUSDT -> SOL。"""
    cleaned = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
        if cleaned.endswith(quote) and len(cleaned) > len(quote):
            return cleaned[: -len(quote)]
    return cleaned


def _fetch_binance_candles(symbol: str, limit: int) -> List[Dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "symbol": symbol.upper(),
            "interval": "1d",
            "limit": max(20, min(int(limit), 500)),
        }
    )
    last_error: Optional[Exception] = None
    for host in BINANCE_HOSTS:
        try:
            rows = json.loads(_get(host + "/api/v3/klines?" + query).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 依次尝试备用域名
            last_error = exc
            continue
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
        if candles:
            return candles
    raise RuntimeError("Binance K 线接口不可用: {0}".format(last_error))


def _fetch_coingecko_history(symbol: str, limit: int) -> List[Dict[str, Any]]:
    asset = _base_asset(symbol)
    coin_id = COINGECKO_IDS.get(asset)
    if not coin_id:
        query = urllib.parse.urlencode({"query": asset})
        data = json.loads(
            _get("https://api.coingecko.com/api/v3/search?" + query).decode("utf-8")
        )
        coins = data.get("coins") or []
        if not coins:
            raise RuntimeError("CoinGecko 未找到资产: {0}".format(asset))
        coin_id = coins[0]["id"]

    days = max(1, min(int(limit), 365))
    query = urllib.parse.urlencode(
        {"vs_currency": "usd", "days": days, "interval": "daily"}
    )
    data = json.loads(
        _get(
            "https://api.coingecko.com/api/v3/coins/{0}/market_chart?".format(coin_id) + query
        ).decode("utf-8")
    )
    prices = data.get("prices") or []
    if not prices:
        raise RuntimeError("CoinGecko 未返回价格序列: {0}".format(asset))

    candles = []
    for timestamp, price in prices:
        candles.append(
            {
                "date": str(date.fromtimestamp(timestamp / 1000)),
                "close": float(price),
            }
        )
    return candles[-int(limit) :]


def fetch_daily_candles(symbol: str, limit: int = 90) -> List[Dict[str, Any]]:
    """拉取交易对最近的历史日线（真实行情）。

    数据源：Binance 公开 K 线接口（主），失败时自动切换备用域名。

    参数：
    - symbol: 交易对，例如 BTCUSDT
    - limit: 拉取根数（20~500）
    """
    return _fetch_binance_candles(symbol, limit)


def fetch_price_history(symbol: str, limit: int = 90) -> List[Dict[str, Any]]:
    """拉取交易对最近的历史日线（真实行情，备选源）。

    数据源：CoinGecko market_chart 接口（当 Binance 不可用时使用）。

    参数：
    - symbol: 交易对，例如 BTCUSDT
    - limit: 拉取根数
    """
    return _fetch_coingecko_history(symbol, limit)


def _fetch_binance_news(limit: int) -> List[Dict[str, Any]]:
    """Binance 资讯（JSON 接口，交易所公告）。"""
    page_size = max(1, min(int(limit), 10))  # 接口对 pageSize 有上限
    query = urllib.parse.urlencode({"type": 1, "pageNo": 1, "pageSize": page_size})
    data = json.loads(
        _get(
            "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?" + query
        ).decode("utf-8")
    )
    items = []
    for row in (data.get("data") or {}).get("catalogs") or []:
        for article in row.get("articles") or []:
            body = (article.get("title") or "") + " " + (article.get("description") or "")
            published = str(article.get("releaseDate") or "")
            if published.isdigit():
                published = datetime.fromtimestamp(int(published) / 1000).strftime("%Y-%m-%d")
            items.append(
                {
                    "title": (article.get("title") or "").strip(),
                    "summary": re.sub(r"<[^>]+>", " ", article.get("description") or "").strip()[:400],
                    "source": "Binance",
                    "published_at": published,
                    "url": article.get("url") or "",
                }
            )
            if len(items) >= limit:
                return items
    return items


def _fetch_rss_news(url: str, source: str, limit: int) -> List[Dict[str, Any]]:
    """RSS 资讯（CoinDesk / Cointelegraph）。"""
    import xml.etree.ElementTree as ET

    raw = _get(url).decode("utf-8", errors="replace")
    root = ET.fromstring(raw)
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        description = re.sub(r"<[^>]+>", " ", node.findtext("description") or "").strip()
        link = (node.findtext("link") or "").strip()
        published = (node.findtext("pubDate") or "").strip()
        items.append(
            {
                "title": title,
                "summary": description[:400],
                "source": source,
                "published_at": published,
                "url": link,
            }
        )
        if len(items) >= limit:
            break
    return items


NEWS_KEYWORD_ALIASES = {
    "BTC": ["BTC", "BITCOIN"],
    "ETH": ["ETH", "ETHEREUM"],
    "SOL": ["SOL", "SOLANA"],
    "BNB": ["BNB"],
    "XRP": ["XRP", "RIPPLE"],
    "ADA": ["ADA", "CARDANO"],
    "DOGE": ["DOGE", "DOGECOIN"],
    "LTC": ["LTC", "LITECOIN"],
    "TON": ["TON", "TONCOIN"],
    "AVAX": ["AVAX", "AVALANCHE"],
    "DOT": ["DOT", "POLKADOT"],
    "MATIC": ["MATIC", "POLYGON"],
    "TRX": ["TRX", "TRON"],
    "LINK": ["LINK", "CHAINLINK"],
    "BCH": ["BCH", "BITCOIN CASH"],
}


def fetch_recent_news(symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
    """拉取交易对最近的相关新闻（真实接口，多源聚合）。

    数据源（按序尝试，失败自动降级）：CoinDesk RSS → Cointelegraph RSS → Binance 资讯；
    按标题去重，并按资产关键词（支持 BTC/Bitcoin 之类的别名）过滤。

    参数：
    - symbol: 交易对，例如 BTCUSDT
    - limit: 返回条数
    """
    wanted = max(1, int(limit))
    base = _base_asset(symbol)
    keywords = NEWS_KEYWORD_ALIASES.get(base, [base.upper()])
    sources = (
        ("coindesk", lambda: _fetch_rss_news(
            "https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk", wanted * 3)),
        ("cointelegraph", lambda: _fetch_rss_news(
            "https://cointelegraph.com/rss", "Cointelegraph", wanted * 3)),
        ("binance", lambda: _fetch_binance_news(wanted)),
    )

    merged: List[Dict[str, Any]] = []
    seen = set()
    errors: List[str] = []
    raw_count = 0
    for _name, loader in sources:
        try:
            fetched = loader()
        except Exception as exc:  # noqa: BLE001 - 单源失败降级到下一个
            errors.append("{0}".format(exc))
            continue
        raw_count += len(fetched)
        for item in fetched:
            haystack = (item.get("title", "") + " " + item.get("summary", "")).upper()
            if not any(keyword in haystack for keyword in keywords):
                continue
            key = item.get("title", "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= wanted:
                return merged

    if merged:
        return merged
    if raw_count:
        # 所有源可用，但没有命中关键词：退回返回各源最新条目（仍是真实新闻）
        fallback: List[Dict[str, Any]] = []
        seen = set()
        for _name, loader in sources:
            try:
                for item in loader():
                    key = item.get("title", "").lower()
                    if key and key not in seen:
                        seen.add(key)
                        fallback.append(item)
                    if len(fallback) >= wanted:
                        return fallback
            except Exception:  # noqa: BLE001
                continue
        if fallback:
            return fallback
    raise RuntimeError("新闻接口全部不可用: {0}".format("; ".join(errors)))


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
                "description": "拉取交易对最近的历史日线收盘价（Binance 公开行情接口）",
                "func": fetch_daily_candles,
            }
        )
        tools.append(
            {
                "name": "fetch_price_history",
                "description": "拉取交易对最近的历史日线收盘价（CoinGecko 公开行情接口，备选源）",
                "func": fetch_price_history,
            }
        )
        tools.append(
            {
                "name": "fetch_recent_news",
                "description": "拉取交易对最近的相关新闻（Binance 资讯 / CoinDesk RSS / Cointelegraph RSS）",
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
