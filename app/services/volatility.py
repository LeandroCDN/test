import math
import statistics
import time

import requests


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
}


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def fetch_candles(asset: str, *, interval: str, limit: int, timeout: float = 2.5) -> list[list]:
    symbol = SYMBOLS.get(str(asset).lower())
    if not symbol:
        return []
    try:
        resp = requests.get(
            BINANCE_KLINES_URL,
            params={"symbol": symbol, "interval": interval, "limit": int(limit)},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def build_snapshot_from_candles(candles: list[list]) -> dict | None:
    if not candles or len(candles) < 3:
        return None

    closes = []
    ranges = []
    for c in candles:
        if not isinstance(c, list) or len(c) < 5:
            continue
        o = _safe_float(c[1], 0.0)
        h = _safe_float(c[2], 0.0)
        l = _safe_float(c[3], 0.0)
        cl = _safe_float(c[4], 0.0)
        if o <= 0 or h <= 0 or l <= 0 or cl <= 0:
            continue
        closes.append(cl)
        ranges.append(max(0.0, (h - l) / o))

    if len(closes) < 3:
        return None

    returns = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        cur = closes[i]
        if prev > 0 and cur > 0:
            returns.append(math.log(cur / prev))
    if len(returns) < 2:
        return None

    ret_std = float(statistics.pstdev(returns))
    mean_range = float(sum(ranges) / max(1, len(ranges)))
    score = (0.70 * ret_std) + (0.30 * mean_range)

    return {
        "score": score,
        "ret_std": ret_std,
        "mean_range": mean_range,
        "candles": len(closes),
        "timestamp": time.time(),
    }


def resolve_regime(score: float, *, low_th: float, high_th: float, extreme_th: float) -> str:
    if score >= extreme_th:
        return "extreme"
    if score >= high_th:
        return "high"
    if score <= low_th:
        return "low"
    return "mid"

