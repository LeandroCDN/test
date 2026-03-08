import json
import requests
from datetime import datetime, timezone
from config import GAMMA_API, MARKET_DURATION_SECONDS


def find_active_btc_5m_market():
    """
    Finds the active BTC Up/Down 5-minute market closest to closing.

    The slug pattern is btc-updown-5m-{unix_timestamp} where the timestamp
    corresponds to the eventStartTime (start of the 5-min window).
    We calculate the current window's timestamp and fetch directly by slug.
    """
    return find_active_crypto_5m_market("btc")


def find_active_eth_5m_market():
    """Convenience wrapper for ETH 5m market discovery."""
    return find_active_crypto_5m_market("eth")


def find_active_sol_5m_market():
    """Convenience wrapper for SOL 5m market discovery."""
    return find_active_crypto_5m_market("sol")


def find_active_crypto_5m_market(asset):
    """
    Finds the active crypto Up/Down 5-minute market closest to closing.

    Supported assets:
      - "btc"
      - "eth"
      - "sol"
    """
    asset = (asset or "").strip().lower()
    if asset not in {"btc", "eth", "sol"}:
        raise ValueError(f"Unsupported asset for 5m market: {asset}")

    now = datetime.now(timezone.utc)
    current_ts = int(now.timestamp())

    # 5-min windows are aligned to :00, :05, :10, etc. in ET,
    # but the unix timestamps are just multiples of 300.
    window_start_ts = (current_ts // MARKET_DURATION_SECONDS) * MARKET_DURATION_SECONDS
    window_end_ts = window_start_ts + MARKET_DURATION_SECONDS

    window_end = datetime.fromtimestamp(window_end_ts, tz=timezone.utc)
    seconds_left = (window_end - now).total_seconds()

    # Try current window first, then next window if current is about to end.
    candidates = [window_start_ts]
    if seconds_left < 5:
        candidates = [window_start_ts + MARKET_DURATION_SECONDS]

    for start_ts in candidates:
        market = _fetch_market_by_timestamp(asset, start_ts)
        if market is not None:
            market["asset"] = asset
            return market

    return None


def _fetch_market_by_timestamp(asset, start_ts):
    """Fetch a specific crypto 5-min market by asset and start timestamp."""
    slug = f"{asset}-updown-5m-{start_ts}"
    url = f"{GAMMA_API}/events/slug/{slug}"

    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        event = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[MARKET] Error fetching event {slug}: {e}")
        return None

    return _parse_event(event)


def _parse_event(event):
    """Parse a Gamma API event into our market dict."""
    markets = event.get("markets", [])
    if not markets:
        return None

    market = markets[0]

    if not market.get("acceptingOrders"):
        return None

    end_date = _parse_date(market.get("endDate"))
    if end_date is None:
        return None

    now = datetime.now(timezone.utc)
    seconds_left = (end_date - now).total_seconds()
    if seconds_left <= 0:
        return None

    clob_raw = market.get("clobTokenIds", "[]")
    try:
        clob_tokens = json.loads(clob_raw) if isinstance(clob_raw, str) else clob_raw
    except json.JSONDecodeError:
        print(f"[MARKET] Failed to parse clobTokenIds: {clob_raw}")
        return None

    if len(clob_tokens) < 2:
        return None

    event_start_time = _parse_date(
        market.get("eventStartTime") or event.get("startTime") or market.get("startDate")
    )

    return {
        "condition_id": market.get("conditionId"),
        "up_token": clob_tokens[0],
        "down_token": clob_tokens[1],
        "end_date": end_date,
        "event_start_time": event_start_time,
        "seconds_left": seconds_left,
        "question": market.get("question", ""),
        "slug": event.get("slug", ""),
        "accepting_orders": market.get("acceptingOrders", False),
    }


def _parse_date(date_str):
    """Parse ISO date string to timezone-aware datetime."""
    if not date_str:
        return None
    try:
        date_str = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None
