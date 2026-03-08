from __future__ import annotations

import math
from typing import Any

import requests

from app import config as shared_cfg
from app.services.volatility import fetch_candles, resolve_regime


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fetch_spot_price(asset: str = "btc", timeout: float = 2.0) -> float | None:
    symbol = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT"}.get(str(asset).lower())
    if not symbol:
        return None
    try:
        response = requests.get(
            shared_cfg.BINANCE_PRICE_URL,
            params={"symbol": symbol},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return float(payload.get("price"))
    except Exception:
        return None


def fetch_reference_snapshot(asset: str = "btc") -> dict[str, float] | None:
    candles = fetch_candles(asset, interval="5m", limit=2)
    if not candles:
        return None
    current_candle = candles[-1]
    if not isinstance(current_candle, list) or len(current_candle) < 5:
        return None
    try:
        open_price = float(current_candle[1])
        close_price = float(current_candle[4])
    except Exception:
        return None
    current_price = fetch_spot_price(asset) or close_price
    if open_price <= 0 or current_price <= 0:
        return None
    return {
        "open_price": open_price,
        "current_price": current_price,
        "distance_pct": (current_price - open_price) / open_price,
    }


def estimate_up_probability(
    *,
    current_price: float,
    open_price: float,
    seconds_left: float,
    sigma_floor_pct: float,
    volatility_snapshot: dict[str, Any] | None,
    regime_multipliers: dict[str, float],
    thresholds: dict[str, float],
) -> tuple[float, str, float]:
    if open_price <= 0 or current_price <= 0:
        return 0.5, "mid", sigma_floor_pct

    regime = "mid"
    regime_mult = regime_multipliers.get("mid", 1.0)
    if volatility_snapshot:
        regime = resolve_regime(
            float(volatility_snapshot.get("score", 0.0)),
            low_th=float(thresholds["low"]),
            high_th=float(thresholds["high"]),
            extreme_th=float(thresholds["extreme"]),
        )
        regime_mult = regime_multipliers.get(regime, regime_mult)

    per_min_vol = sigma_floor_pct
    if volatility_snapshot:
        per_min_vol = max(
            sigma_floor_pct,
            float(volatility_snapshot.get("ret_std", sigma_floor_pct)) * regime_mult,
        )
    remaining_vol_pct = max(
        sigma_floor_pct,
        per_min_vol * math.sqrt(max(seconds_left, 1.0) / 60.0),
    )
    distance_pct = (current_price - open_price) / open_price
    z_score = distance_pct / remaining_vol_pct if remaining_vol_pct > 0 else 0.0
    probability = 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
    return clamp(probability, 0.01, 0.99), regime, remaining_vol_pct


def evaluate_trade_setup(
    *,
    market: dict[str, Any],
    prices: dict[str, float],
    bids: dict[str, float],
    fair_up: float,
    min_edge: float,
    max_odds: float,
    max_spread: float,
    min_model_probability: float,
    min_market_probability: float,
) -> dict[str, Any] | None:
    up_token = str(market["up_token"])
    down_token = str(market["down_token"])
    up_ask = prices.get(up_token)
    down_ask = prices.get(down_token)
    up_bid = bids.get(up_token)
    down_bid = bids.get(down_token)
    fair_down = 1.0 - fair_up
    candidates: list[dict[str, Any]] = []
    side_evaluations: list[dict[str, Any]] = []
    for side, ask, bid, fair_value, token_id in (
        ("up", up_ask, up_bid, fair_up, market["up_token"]),
        ("down", down_ask, down_bid, fair_down, market["down_token"]),
    ):
        spread = max(0.0, ask - bid) if isinstance(ask, (float, int)) and isinstance(bid, (float, int)) else 0.0
        edge = (fair_value - ask) if isinstance(ask, (float, int)) else -1.0
        checks = {
            "has_price": isinstance(ask, (float, int)),
            "price_in_range": isinstance(ask, (float, int)) and ask > 0 and ask < max_odds,
            "market_probability_ok": isinstance(ask, (float, int)) and ask >= min_market_probability,
            "spread_ok": spread <= max_spread,
            "edge_ok": edge >= min_edge,
            "model_probability_ok": fair_value >= min_model_probability,
        }
        eligible = all(checks.values())
        reason = "Eligible"
        if not checks["has_price"]:
            reason = "No market price available"
        elif not checks["price_in_range"]:
            reason = f"Price outside allowed range (max {max_odds:.2f})"
        elif not checks["market_probability_ok"]:
            reason = f"Market {ask:.1%} below minimum {min_market_probability:.1%}"
        elif not checks["spread_ok"]:
            reason = f"Spread {spread:.1%} above limit {max_spread:.1%}"
        elif not checks["edge_ok"]:
            reason = f"Edge {edge:.1%} below minimum {min_edge:.1%}"
        elif not checks["model_probability_ok"]:
            reason = f"Model {fair_value:.1%} below minimum {min_model_probability:.1%}"

        side_eval = {
            "side": side,
            "buy_price": ask,
            "best_bid": bid,
            "fair_value": fair_value,
            "edge": edge,
            "spread": spread,
            "eligible": eligible,
            "reason": reason,
            "checks": checks,
            "token_id": token_id,
        }
        side_evaluations.append(side_eval)
        if eligible:
            candidates.append(
                {
                    "asset": str(market.get("asset") or "btc"),
                    "market": market,
                    "side": side,
                    "token_id": token_id,
                    "buy_price": ask,
                    "best_bid": bid,
                    "fair_value": fair_value,
                    "edge": edge,
                    "spread": spread,
                }
            )

    best_side = None
    if side_evaluations:
        best_side = max(side_evaluations, key=lambda item: item["edge"])

    candidate = None
    if candidates:
        candidates.sort(key=lambda item: (item["edge"], -item["buy_price"]), reverse=True)
        candidate = candidates[0]

    return {
        "candidate": candidate,
        "best_side": best_side,
        "sides": {item["side"]: item for item in side_evaluations},
        "decision": "eligible" if candidate else "watching",
        "reason": candidate["side"].upper() + " meets all entry filters"
        if candidate
        else (best_side["reason"] if best_side else "No market prices available"),
    }


def select_trade_candidate(
    *,
    market: dict[str, Any],
    prices: dict[str, float],
    bids: dict[str, float],
    fair_up: float,
    min_edge: float,
    max_odds: float,
    max_spread: float,
    min_model_probability: float,
    min_market_probability: float,
) -> dict[str, Any] | None:
    evaluation = evaluate_trade_setup(
        market=market,
        prices=prices,
        bids=bids,
        fair_up=fair_up,
        min_edge=min_edge,
        max_odds=max_odds,
        max_spread=max_spread,
        min_model_probability=min_model_probability,
        min_market_probability=min_market_probability,
    )
    return evaluation["candidate"]
