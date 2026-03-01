"""
Pure entry-strategy helpers shared by bot_manager (dashboard) and the CLI bot.

Functions here are stateless and only depend on market data / config values
passed in as arguments — no side effects, no imports of trader or market modules.
"""

from __future__ import annotations

from app import config as cfg


def evaluate_candidate_market(
    market: dict,
    prices: dict,
    *,
    min_odds: float,
    max_odds: float | None = None,
) -> dict | None:
    """Score a single market against current prices and odds policy.

    Returns a candidate dict if prices are available, else ``None``.
    The ``accepted`` flag indicates whether the candidate passes the odds filter.
    """
    if max_odds is None:
        max_odds = cfg.MAX_ODDS

    up_price = prices.get(str(market["up_token"]))
    down_price = prices.get(str(market["down_token"]))
    if up_price is None or down_price is None:
        return None

    if up_price >= down_price:
        side, token_id, buy_price = "up", market["up_token"], up_price
    else:
        side, token_id, buy_price = "down", market["down_token"], down_price

    accepted = min_odds <= buy_price < max_odds
    rejection_reason = None
    if not accepted:
        rejection_reason = "too_high" if buy_price >= max_odds else "too_low"
    return {
        "asset": market.get("asset", ""),
        "market": market,
        "side": side,
        "token_id": token_id,
        "buy_price": buy_price,
        "up_price": up_price,
        "down_price": down_price,
        "accepted": accepted,
        "rejection_reason": rejection_reason,
        "edge": buy_price - min_odds,
    }


def pick_best_candidate(candidates: list[dict]) -> dict | None:
    """Choose the single best entry among evaluated candidates.

    Prefers higher edge, then lower buy price, then BTC over ETH for stability.
    """
    accepted = [c for c in candidates if c and c["accepted"]]
    if not accepted:
        return None

    priority = {"btc": 0, "eth": 1}
    accepted.sort(
        key=lambda c: (c["edge"], -c["buy_price"], -priority.get(c["asset"], 99)),
        reverse=True,
    )
    return accepted[0]


def get_dynamic_entry_params(
    seconds_left: float,
    profile_points: list | None = None,
) -> dict:
    """Interpolate min_odds and capital_pct from the entry profile curve.

    ``profile_points`` defaults to the static ``ENTRY_PROFILE_POINTS`` in config
    when not supplied (useful for the CLI bot).
    """
    if profile_points is None:
        profile_points = cfg.ENTRY_PROFILE_POINTS

    points = sorted(profile_points, key=lambda x: x[0], reverse=True)
    if not points:
        return {"min_odds": 0.88, "capital_pct": 0.20}

    if seconds_left >= points[0][0]:
        return {"min_odds": points[0][1], "capital_pct": points[0][2]}
    if seconds_left <= points[-1][0]:
        return {"min_odds": points[-1][1], "capital_pct": points[-1][2]}

    for i in range(len(points) - 1):
        hi_s, hi_odds, hi_pct = points[i]
        lo_s, lo_odds, lo_pct = points[i + 1]
        if hi_s >= seconds_left >= lo_s:
            span = hi_s - lo_s
            if span <= 0:
                return {"min_odds": lo_odds, "capital_pct": lo_pct}
            w = (hi_s - seconds_left) / span
            min_odds = hi_odds + (lo_odds - hi_odds) * w
            capital_pct = hi_pct + (lo_pct - hi_pct) * w
            return {"min_odds": min_odds, "capital_pct": capital_pct}

    return {"min_odds": points[-1][1], "capital_pct": points[-1][2]}
