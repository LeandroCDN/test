import os
import sys
import time
import math
from datetime import datetime, timezone

from dotenv import load_dotenv
from colorama import init as colorama_init, Fore, Style

from config import (
    ENTRY_START_SECONDS,
    ENTRY_CHECK_INTERVAL_SECONDS,
    ENTRY_PROFILE_POINTS,
    ENTRY_BALANCE_REFRESH_SECONDS,
    ENTRY_CHECK_INTERVAL_FAST_SECONDS,
    ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS,
    MIN_BET_USDC,
    POLL_INTERVAL_SECONDS,
    POST_RESOLUTION_BUFFER_SECONDS,
    MAX_ODDS,
    WAIT_LOG_INTERVAL_SECONDS,
    AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE,
    LOG_LEVEL,
    AUTO_REDEEM_ERROR_LOG_COOLDOWN_SECONDS,
    STOP_LOSS_ENABLED,
    STOP_LOSS_PCT,
    STOP_LOSS_POLL_SECONDS,
    STOP_LOSS_CONFIRM_TICKS,
    STOP_LOSS_RETRY_SECONDS,
    FILL_SLIPPAGE_WARN_PCT,
)
from market import find_active_crypto_5m_market
from trader import (
    init_client,
    get_balance,
    place_bet,
    get_token_prices_batch,
    get_token_bid,
    sell_shares,
    get_entry_fill_details,
    init_auto_redeemer,
)

colorama_init()

DIM = Style.DIM
RESET = Style.RESET_ALL
GREEN = Fore.GREEN
RED = Fore.RED
YELLOW = Fore.YELLOW
CYAN = Fore.CYAN
WHITE = Fore.WHITE
BRIGHT = Style.BRIGHT


_LOG_LEVEL_ORDER = {"quiet": 0, "normal": 1, "debug": 2}
_CURRENT_LOG_LEVEL = _LOG_LEVEL_ORDER.get(str(LOG_LEVEL).lower(), 1)
_LAST_REDEEM_ERROR_LOG = {"message": None, "ts": 0.0}


def _can_log(level):
    wanted = _LOG_LEVEL_ORDER.get(str(level).lower(), 1)
    return _CURRENT_LOG_LEVEL >= wanted


def log(msg, color="", level="normal"):
    if not _can_log(level):
        return
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{DIM}[{ts}]{RESET} {color}{msg}{RESET}")


def new_stats():
    return {
        "total_rounds": 0,
        "total_entries": 0,
        "total_btc_entries": 0,
        "total_eth_entries": 0,
        "total_wins": 0,
        "total_losses": 0,
        "total_stop_exits": 0,
        "total_stop_wins": 0,
        "total_stop_losses": 0,
        "total_skipped": 0,
        "total_pnl": 0.0,
        "start_balance": 0.0,
        "start_time": datetime.now(),
    }


def run_bot(dry_run=False):
    load_dotenv()

    private_key = os.getenv("PK")
    if not private_key:
        print(f"{RED}ERROR: PK not found in .env file. See .env.example for setup.{RESET}")
        sys.exit(1)

    signature_type = int(os.getenv("SIGNATURE_TYPE", "0"))
    funder = os.getenv("FUNDER")

    log("Initializing Polymarket client...", DIM)
    client = init_client(private_key, signature_type=signature_type, funder=funder)
    auto_redeemer = init_auto_redeemer(private_key=private_key, funder=funder)

    balance = get_balance(client)
    stats = new_stats()
    stats["start_balance"] = balance

    print()
    log(f"{BRIGHT}{'=' * 50}")
    log(f"{BRIGHT}  BTC 5-Min Polymarket Bot")
    log(f"{BRIGHT}{'=' * 50}")
    log(f"  Balance:    {GREEN}${balance:.2f} USDC{RESET}")
    log(
        f"  Entry:      dynamic BTC+ETH from last {ENTRY_START_SECONDS}s (every {ENTRY_CHECK_INTERVAL_SECONDS}s)"
    )
    log(f"  Odds:       dynamic min odds | Max odds: {MAX_ODDS:.0%} | Min bet: ${MIN_BET_USDC}")
    if dry_run:
        log(f"  Mode:       {YELLOW}DRY RUN (no real orders){RESET}")
    log(f"{BRIGHT}{'=' * 50}")
    print()

    if balance < MIN_BET_USDC:
        log(f"Balance too low (min ${MIN_BET_USDC}). Exiting.", RED)
        sys.exit(1)

    while True:
        try:
            _run_single_round(client, dry_run, stats, auto_redeemer)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"Unexpected error: {e}", RED)
            log("Retrying in 10 seconds...", DIM)
            time.sleep(10)

    _print_session_summary(stats, client)


def _run_single_round(client, dry_run, stats, auto_redeemer):
    stats["total_rounds"] += 1
    rnd = stats["total_rounds"]
    balance = get_balance(client)

    # Try to redeem previously resolved positions before entering a new market.
    _attempt_auto_redeem(auto_redeemer, log_unavailable=True)

    markets = None
    while markets is None:
        markets = _get_active_round_markets()
        if markets is None:
            _attempt_auto_redeem(auto_redeemer)
            time.sleep(POLL_INTERVAL_SECONDS)

    now = datetime.now(timezone.utc)
    ref_seconds_left = _get_round_seconds_left(markets, now)
    round_markets_info = " | ".join(
        f"{asset.upper()}:{m['slug']}" for asset, m in sorted(markets.items())
    )
    log(
        f"{BRIGHT}--- Round {rnd} ---  {CYAN}BTC/ETH 5m candidate scan{RESET}  ({ref_seconds_left:.0f}s left)  {DIM}{round_markets_info}{RESET}"
    )

    # --- Phase 1: Wait until dynamic entry starts ---
    now = datetime.now(timezone.utc)
    seconds_left = _get_round_seconds_left(markets, now)

    if seconds_left > ENTRY_START_SECONDS:
        wait_remaining = seconds_left - ENTRY_START_SECONDS
        log(f"Waiting {wait_remaining:.0f}s until dynamic entry phase...", DIM, level="debug")
        while wait_remaining > 0:
            chunk = min(WAIT_LOG_INTERVAL_SECONDS, wait_remaining)
            time.sleep(chunk)
            wait_remaining -= chunk
            _attempt_auto_redeem(auto_redeemer)
            if wait_remaining > 0:
                log(f"  {wait_remaining:.0f}s remaining...", DIM, level="debug")

    # --- Phase 2: Dynamic entry loop ---
    entry = _attempt_dynamic_entry(client, markets, auto_redeemer, dry_run=dry_run)
    if entry is None:
        _skip_round(
            next(iter(markets.values())),
            stats,
            auto_redeemer,
            "No entry in dynamic window (BTC/ETH)",
        )
        return

    market = entry["market"]
    selected_asset = entry["asset"]

    token_id = entry["token_id"]
    buy_price = entry["quoted_price"]
    bet_amount = entry["bet_amount"]
    min_odds_at_entry = entry["min_odds_at_entry"]
    capital_pct_at_entry = entry["capital_pct_at_entry"]
    seconds_left_at_entry = entry["seconds_left_at_entry"]

    # --- Phase 3: Post-order handling ---
    stats["total_entries"] += 1
    if selected_asset == "btc":
        stats["total_btc_entries"] += 1
    elif selected_asset == "eth":
        stats["total_eth_entries"] += 1
    result = entry["order_response"]
    stop_info = {"triggered": False}

    if result is None:
        log("Order failed.", RED)
    else:
        fill = get_entry_fill_details(
            client=client,
            order_response=result,
            token_id=token_id,
            fallback_price=buy_price,
            fallback_amount=bet_amount,
        )
        entry_price = fill["entry_price"]
        shares = fill["shares"]
        source = fill["source"]
        stop_price = entry_price * (1 - STOP_LOSS_PCT)
        slippage_pct = ((entry_price - buy_price) / buy_price) if buy_price > 0 else 0.0

        log(
            f"Entry fill [{selected_asset.upper()}]: {shares:.2f} shares @ ${entry_price:.3f} ({source}) | SL ${stop_price:.3f}",
            DIM,
            level="debug",
        )
        log(
            f"Entry profile used: t={seconds_left_at_entry:.1f}s min_odds={min_odds_at_entry:.3f} capital={capital_pct_at_entry:.0%}",
            DIM,
            level="debug",
        )

        fill_out_of_policy = entry_price < min_odds_at_entry or entry_price >= MAX_ODDS
        if fill_out_of_policy:
            log(
                f"{YELLOW}FILL WARNING{RESET}: fill ${entry_price:.3f} outside policy [{min_odds_at_entry:.3f}, {MAX_ODDS:.3f})",
                YELLOW,
            )
        if slippage_pct >= FILL_SLIPPAGE_WARN_PCT:
            log(
                f"{YELLOW}SLIPPAGE WARNING{RESET}: quote ${buy_price:.3f} -> fill ${entry_price:.3f} ({slippage_pct * 100:.1f}%)",
                YELLOW,
            )

        if (not dry_run) and STOP_LOSS_ENABLED and shares > 0 and entry_price > 0:
            stop_info = _monitor_stop_loss_until_resolution(
                client=client,
                market=market,
                auto_redeemer=auto_redeemer,
                token_id=token_id,
                shares=shares,
                entry_price=entry_price,
            )

    # --- Phase 5: Wait for resolution ---
    _wait_for_resolution(market, auto_redeemer)

    # --- Phase 6: Log result (positions will be sold at start of next round) ---
    new_balance = get_balance(client)
    pnl = new_balance - balance

    if dry_run:
        log(f"{DIM}DRY RUN — round complete{RESET} | Balance: ${new_balance:.2f}")
        stats["total_wins"] += 1
    elif stop_info.get("triggered"):
        stats["total_stop_exits"] += 1
        stats["total_pnl"] += pnl
        if pnl > 0:
            stats["total_wins"] += 1
            stats["total_stop_wins"] += 1
            log(
                f"{GREEN}{BRIGHT}WIN (STOP){RESET}  PnL: {GREEN}+${pnl:.2f}{RESET} | Balance: ${new_balance:.2f}"
            )
        elif pnl == 0:
            log(f"EVEN (STOP)  PnL: $0.00 | Balance: ${new_balance:.2f}")
        else:
            stats["total_losses"] += 1
            stats["total_stop_losses"] += 1
            log(
                f"{RED}{BRIGHT}LOSS (STOP){RESET} PnL: {RED}-${abs(pnl):.2f}{RESET} | Balance: ${new_balance:.2f}"
            )
    elif pnl > 0:
        stats["total_pnl"] += pnl
        stats["total_wins"] += 1
        log(f"{GREEN}{BRIGHT}WIN{RESET}  PnL: {GREEN}+${pnl:.2f}{RESET} | Balance: ${new_balance:.2f}")
    elif pnl == 0:
        log(f"EVEN  PnL: $0.00 | Balance: ${new_balance:.2f} (awaiting redeem)")
    else:
        log(
            f"UNSETTLED  Available balance: ${new_balance:.2f} (redeem pending, skipping LOSS classification)",
            YELLOW,
        )
    print()


def _get_round_seconds_left(markets, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    return min((m["end_date"] - now).total_seconds() for m in markets.values())


def _get_active_round_markets():
    found = {}
    for asset in ("btc", "eth"):
        market = find_active_crypto_5m_market(asset)
        if market is not None:
            found[asset] = market
    return found or None


def _evaluate_candidate_market(market, prices, min_odds):
    up_price = prices.get(str(market["up_token"]))
    down_price = prices.get(str(market["down_token"]))
    if up_price is None or down_price is None:
        return None

    if up_price >= down_price:
        side, token_id, buy_price = "up", market["up_token"], up_price
    else:
        side, token_id, buy_price = "down", market["down_token"], down_price

    accepted = min_odds <= buy_price < MAX_ODDS
    return {
        "asset": market.get("asset", ""),
        "market": market,
        "side": side,
        "token_id": token_id,
        "buy_price": buy_price,
        "up_price": up_price,
        "down_price": down_price,
        "accepted": accepted,
        "edge": buy_price - min_odds,
    }


def _pick_best_candidate(candidates):
    accepted = [c for c in candidates if c and c["accepted"]]
    if not accepted:
        return None

    # Prefer higher edge; tie-breaker: lower buy price; then stable BTC-first.
    priority = {"btc": 0, "eth": 1}
    accepted.sort(
        key=lambda c: (c["edge"], -c["buy_price"], -priority.get(c["asset"], 99)),
        reverse=True,
    )
    return accepted[0]


def _get_dynamic_entry_params(seconds_left):
    points = sorted(ENTRY_PROFILE_POINTS, key=lambda x: x[0], reverse=True)
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


def _attempt_dynamic_entry(client, markets, auto_redeemer, dry_run=False):
    balance_snapshot = None
    balance_snapshot_ts = 0.0

    while True:
        now = datetime.now(timezone.utc)
        seconds_left = _get_round_seconds_left(markets, now)
        if seconds_left <= 0:
            return None

        params = _get_dynamic_entry_params(seconds_left)
        min_odds = params["min_odds"]
        capital_pct = params["capital_pct"]

        token_ids = []
        for m in markets.values():
            token_ids.extend([m["up_token"], m["down_token"]])
        prices = get_token_prices_batch(client, token_ids, side="BUY")

        candidates = []
        for asset, market in sorted(markets.items()):
            candidate = _evaluate_candidate_market(market, prices, min_odds=min_odds)
            if candidate:
                candidates.append(candidate)

        chosen = _pick_best_candidate(candidates)
        if chosen is not None:
            now_ts = time.time()
            need_balance_refresh = (
                balance_snapshot is None
                or (now_ts - balance_snapshot_ts) >= ENTRY_BALANCE_REFRESH_SECONDS
            )
            if need_balance_refresh:
                balance_snapshot = get_balance(client)
                balance_snapshot_ts = now_ts

            balance = balance_snapshot
            target_amount = max(MIN_BET_USDC, balance * capital_pct)
            bet_amount = math.floor(target_amount * 100) / 100
            if bet_amount > balance:
                bet_amount = math.floor(balance * 100) / 100

            if bet_amount < MIN_BET_USDC:
                return None

            log(
                f"Entry signal [{seconds_left:.0f}s] {chosen['asset'].upper()}: {chosen['side'].upper()} odds={chosen['buy_price']:.3f} min={min_odds:.3f} | Bet ${bet_amount:.2f} ({capital_pct:.0%})",
                CYAN,
            )
            order_response = place_bet(
                client,
                chosen["token_id"],
                bet_amount,
                dry_run=dry_run,
            )
            if order_response is not None:
                return {
                    "asset": chosen["asset"],
                    "market": chosen["market"],
                    "token_id": chosen["token_id"],
                    "quoted_price": chosen["buy_price"],
                    "bet_amount": bet_amount,
                    "min_odds_at_entry": min_odds,
                    "capital_pct_at_entry": capital_pct,
                    "seconds_left_at_entry": seconds_left,
                    "order_response": order_response,
                }
            log("Order failed, continuing dynamic retries...", YELLOW, level="debug")
        else:
            details = []
            for c in candidates:
                details.append(
                    f"{c['asset'].upper()} {c['side'].upper()}={c['buy_price']:.3f} (<{min_odds:.3f} or >= {MAX_ODDS:.3f})"
                )
            msg = " | ".join(details) if details else "no prices"
            log(
                f"Check [{seconds_left:.0f}s]: no eligible entry ({msg})",
                DIM,
                level="debug",
            )

        redeem = _attempt_auto_redeem(auto_redeemer, return_result=True)
        if redeem and redeem.get("claimed", 0) > 0:
            balance_snapshot = get_balance(client)
            balance_snapshot_ts = time.time()

        sleep_s = (
            ENTRY_CHECK_INTERVAL_FAST_SECONDS
            if seconds_left <= ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS
            else ENTRY_CHECK_INTERVAL_SECONDS
        )
        time.sleep(sleep_s)


def _skip_round(market, stats, auto_redeemer, reason):
    stats["total_skipped"] += 1
    log(f"{YELLOW}SKIP:{RESET} {reason}")
    _wait_for_resolution(market, auto_redeemer)
    print()


def _wait_for_resolution(market, auto_redeemer):
    now = datetime.now(timezone.utc)
    seconds_left = (market["end_date"] - now).total_seconds()
    wait = max(0, seconds_left) + POST_RESOLUTION_BUFFER_SECONDS
    log(f"Waiting {wait:.0f}s for resolution...", DIM, level="debug")
    wait_remaining = wait
    while wait_remaining > 0:
        chunk = min(WAIT_LOG_INTERVAL_SECONDS, wait_remaining)
        time.sleep(chunk)
        wait_remaining -= chunk
        _attempt_auto_redeem(auto_redeemer)
        if wait_remaining > 0:
            log(f"  {wait_remaining:.0f}s remaining...", DIM, level="debug")


def _attempt_auto_redeem(auto_redeemer, log_unavailable=False, return_result=False):
    redeem = auto_redeemer.redeem_once(
        max_conditions=AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE
    )

    if not redeem["attempted"]:
        if log_unavailable and redeem["errors"]:
            log(f"Auto-redeem unavailable: {redeem['errors'][0]}", YELLOW)
        return redeem if return_result else None

    if redeem["claimed"] > 0 or redeem["pending"] > 0 or redeem["errors"]:
        show_summary = redeem["claimed"] > 0 or redeem["pending"] > 0
        if show_summary:
            log(
                f"Auto-redeem: claimed={redeem['claimed']} pending={redeem['pending']} errors={len(redeem['errors'])}",
                CYAN if redeem["claimed"] > 0 else YELLOW,
            )
        if redeem["errors"]:
            now_ts = time.time()
            first_error = redeem["errors"][0]
            should_log_error = (
                first_error != _LAST_REDEEM_ERROR_LOG["message"]
                or now_ts - _LAST_REDEEM_ERROR_LOG["ts"] >= AUTO_REDEEM_ERROR_LOG_COOLDOWN_SECONDS
            )
            if should_log_error:
                log(f"Auto-redeem detail: {first_error}", DIM, level="debug")
                _LAST_REDEEM_ERROR_LOG["message"] = first_error
                _LAST_REDEEM_ERROR_LOG["ts"] = now_ts
    return redeem if return_result else None


def _monitor_stop_loss_until_resolution(
    client,
    market,
    auto_redeemer,
    token_id,
    shares,
    entry_price,
):
    stop_price = entry_price * (1 - STOP_LOSS_PCT)
    confirmations = 0
    next_sell_attempt_ts = 0.0
    last_debug_ts = 0.0

    while True:
        now = datetime.now(timezone.utc)
        seconds_left = (market["end_date"] - now).total_seconds()
        if seconds_left <= 0:
            return {"triggered": False}

        bid = get_token_bid(client, token_id)
        if bid is not None and bid <= stop_price:
            confirmations += 1
        else:
            confirmations = 0

        if bid is not None and time.time() - last_debug_ts >= WAIT_LOG_INTERVAL_SECONDS:
            log(
                f"SL monitor: bid=${bid:.3f} stop=${stop_price:.3f} confirms={confirmations}/{STOP_LOSS_CONFIRM_TICKS}",
                DIM,
                level="debug",
            )
            last_debug_ts = time.time()

        if confirmations >= STOP_LOSS_CONFIRM_TICKS and time.time() >= next_sell_attempt_ts:
            log(
                f"{YELLOW}STOP-LOSS trigger{RESET}: bid ${bid:.3f} <= stop ${stop_price:.3f}. Selling {shares:.2f} shares...",
                YELLOW,
            )
            sell_result = sell_shares(client, token_id, shares, dry_run=False)
            if sell_result is not None:
                return {"triggered": True, "exit_bid": bid, "stop_price": stop_price}

            log("Stop-loss sell failed, retrying...", YELLOW, level="debug")
            next_sell_attempt_ts = time.time() + STOP_LOSS_RETRY_SECONDS
            confirmations = 0

        _attempt_auto_redeem(auto_redeemer)
        time.sleep(STOP_LOSS_POLL_SECONDS)


def _print_session_summary(stats, client):
    duration = datetime.now() - stats["start_time"]
    hours = duration.total_seconds() / 3600
    minutes = duration.total_seconds() / 60

    entries = stats["total_entries"]
    win_rate = (stats["total_wins"] / entries * 100) if entries > 0 else 0

    current_balance = get_balance(client)
    session_pnl = current_balance - stats["start_balance"]

    print()
    log(f"{BRIGHT}{'=' * 50}")
    log(f"{BRIGHT}  SESSION SUMMARY")
    log(f"{BRIGHT}{'=' * 50}")

    if minutes < 60:
        log(f"  Duration:     {minutes:.0f}m")
    else:
        log(f"  Duration:     {hours:.1f}h")

    log(f"  Rounds:       {stats['total_rounds']}")
    log(f"  Entries:      {entries} bets placed")
    log(f"    BTC entries:{stats['total_btc_entries']}")
    log(f"    ETH entries:{stats['total_eth_entries']}")
    log(f"  Skipped:      {stats['total_skipped']}")
    log(f"  Stop exits:   {stats['total_stop_exits']}")

    if entries > 0:
        log(f"  Wins:         {GREEN}{stats['total_wins']}{RESET}")
        log(f"  Losses:       {RED}{stats['total_losses']}{RESET}")
        log(f"  Win rate:     {BRIGHT}{win_rate:.1f}%{RESET}")
        if stats["total_stop_exits"] > 0:
            log(f"  Stop wins:    {GREEN}{stats['total_stop_wins']}{RESET}")
            log(f"  Stop losses:  {RED}{stats['total_stop_losses']}{RESET}")

    pnl_color = GREEN if session_pnl >= 0 else RED
    log(f"  Start:        ${stats['start_balance']:.2f}")
    log(f"  Current:      ${current_balance:.2f}")
    log(f"  Session PnL:  {pnl_color}{BRIGHT}{'+'if session_pnl >= 0 else ''}${session_pnl:.2f}{RESET}")

    log(f"{BRIGHT}{'=' * 50}")
    print()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "--dry" in sys.argv
    run_bot(dry_run=dry)
