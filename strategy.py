import requests
from config import BINANCE_PRICE_URL


def get_btc_price():
    """Fetch current BTC/USDT price from Binance."""
    try:
        resp = requests.get(
            BINANCE_PRICE_URL, params={"symbol": "BTCUSDT"}, timeout=5
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"[STRATEGY] Error fetching BTC price: {e}")
        return None


def decide_side(current_price, price_to_beat):
    """
    Decide whether to buy Up or Down based on current BTC price
    vs the opening price of the 5-minute window.

    Returns "up" if BTC >= opening price, "down" otherwise.
    """
    if current_price >= price_to_beat:
        return "up"
    else:
        return "down"


