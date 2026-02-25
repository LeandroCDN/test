"""
Quick validation script - tests market discovery and price fetching
without needing a Polymarket account or private key.
Run: python test_market.py
"""
from datetime import datetime, timezone
from market import find_active_btc_5m_market
from strategy import get_btc_price, decide_side


def main():
    print("=" * 60)
    print("BTC 5-Min Bot - Component Test")
    print("=" * 60)

    # Test 1: BTC price from Binance
    print("\n[TEST 1] Fetching BTC price from Binance...")
    price = get_btc_price()
    if price:
        print(f"  OK - BTC/USDT: ${price:,.2f}")
    else:
        print("  FAIL - Could not fetch BTC price")
        return

    # Test 2: Find active market
    print("\n[TEST 2] Searching for active BTC 5-min market...")
    market = find_active_btc_5m_market()
    if market:
        print(f"  OK - Found market:")
        print(f"    Question: {market['question']}")
        print(f"    Slug: {market['slug']}")
        print(f"    End: {market['end_date'].strftime('%H:%M:%S UTC')}")
        print(f"    Seconds left: {market['seconds_left']:.0f}s")
        print(f"    Up token: {market['up_token'][:20]}...")
        print(f"    Down token: {market['down_token'][:20]}...")
        print(f"    Accepting orders: {market['accepting_orders']}")
        if market.get("event_start_time"):
            print(f"    Event start: {market['event_start_time'].strftime('%H:%M:%S UTC')}")
    else:
        print("  WARN - No active market found (may be between windows)")
        print("  This is normal if no 5-min window is currently open.")

    # Test 3: Decision logic
    print("\n[TEST 3] Testing decision logic...")
    opening = 95000.0
    current_up = 95050.0
    current_down = 94950.0

    side_up = decide_side(current_up, opening)
    side_down = decide_side(current_down, opening)
    side_equal = decide_side(opening, opening)

    assert side_up == "up", f"Expected 'up', got '{side_up}'"
    assert side_down == "down", f"Expected 'down', got '{side_down}'"
    assert side_equal == "up", f"Expected 'up' for equal, got '{side_equal}'"
    print("  OK - All decision logic tests passed")

    print("\n" + "=" * 60)
    print("All component tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
