"""
Quick validation script - tests market discovery and price fetching
without needing a Polymarket account or private key.
Run: python test/test_market.py
"""
from datetime import datetime, timezone
from market import find_active_btc_5m_market


def main():
    print("=" * 60)
    print("BTC 5-Min Bot - Component Test")
    print("=" * 60)

    # Test 1: Find active market
    print("\n[TEST 1] Searching for active BTC 5-min market...")
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

    # Test 2: Entry strategy helpers
    print("\n[TEST 2] Testing entry strategy helpers...")
    from app.services.entry_strategy import (
        evaluate_candidate_market,
        pick_best_candidate,
        get_dynamic_entry_params,
    )

    params_30s = get_dynamic_entry_params(30.0)
    params_5s = get_dynamic_entry_params(5.0)
    assert params_30s["min_odds"] >= params_5s["min_odds"], "min_odds should decrease over time"
    print(f"  OK - @30s: min_odds={params_30s['min_odds']:.3f} cap={params_30s['capital_pct']:.0%}")
    print(f"  OK -  @5s: min_odds={params_5s['min_odds']:.3f} cap={params_5s['capital_pct']:.0%}")

    mock_market = {"up_token": "tok_up", "down_token": "tok_dn", "asset": "btc"}
    prices = {"tok_up": 0.90, "tok_dn": 0.10}
    cand = evaluate_candidate_market(mock_market, prices, min_odds=0.85)
    assert cand is not None and cand["side"] == "up" and cand["accepted"]
    print("  OK - evaluate_candidate_market works")

    best = pick_best_candidate([cand])
    assert best is not None and best["asset"] == "btc"
    print("  OK - pick_best_candidate works")

    print("\n" + "=" * 60)
    print("All component tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
