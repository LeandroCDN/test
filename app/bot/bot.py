"""
Standalone CLI bot — thin wrapper around shared modules.

Runs the same trading logic as the dashboard worker but prints to the console
with colorama instead of pushing events to the state store.

Usage:
    python app/bot/bot.py [--dry-run]
    python scripts/run_bot.py [--dry-run]
"""

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
    ENTRY_CHECK_INTERVAL_FAST_SECONDS,
    ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS,
    ENTRY_BALANCE_REFRESH_SECONDS,
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
    ENTRY_LOCK_MARKET_ON_HIGH_ODDS_REJECT,
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
from app.services.entry_strategy import (
    evaluate_candidate_market,
    pick_best_candidate,
    get_dynamic_entry_params,
)

colorama_init()

DIM = Style.DIM
RESET = Style.RESET_ALL
GREEN = Fore.GREEN
RED = Fore.RED
YELLOW = Fore.YELLOW
CYAN = Fore.CYAN
BRIGHT = Style.BRIGHT

_LOG_LEVEL_ORDER = {"quiet": 0, "normal": 1, "debug": 2}
_CURRENT_LOG_LEVEL = _LOG_LEVEL_ORDER.get(str(LOG_LEVEL).lower(), 1)
_LAST_REDEEM_ERROR_LOG = {"message": None, "ts": 0.0}


def _can_log(level):
    return _CURRENT_LOG_LEVEL >= _LOG_LEVEL_ORDER.get(str(level).lower(), 1)


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


# ── main entry ────────────────────────────────────────────────────


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
    log(f"{BRIGHT}  BTC/ETH 5-Min Polymarket Bot (CLI)")
    log(f"{BRIGHT}{'=' * 50}")
    log(f"  Balance:    {GREEN}${balance:.2f} USDC{RESET}")
    log(f"  Entry:      dynamic BTC+ETH from last {ENTRY_START_SECONDS}s (every {ENTRY_CHECK_INTERVAL_SECONDS}s)")
    log(f"  Odds:       dynamic | Max {MAX_ODDS:.0%} | Min bet ${MIN_BET_USDC}")
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


# ── round logic ───────────────────────────────────────────────────


def _get_active_round_markets():
    found = {}
    for asset in ("btc", "eth"):
        market = find_active_crypto_5m_market(asset)
        if market is not None:
            found[asset] = market
    return found or None


def _round_seconds_left(markets, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    return min((m["end_date"] - now).total_seconds() for m in markets.values())


def _run_single_round(client, dry_run, stats, auto_redeemer):
    stats["total_rounds"] += 1
    rnd = stats["total_rounds"]
    balance = get_balance(client)

    _attempt_auto_redeem(auto_redeemer, log_unavailable=True)

    markets = None
    while markets is None:
        markets = _get_active_round_markets()
        if markets is None:
            _attempt_auto_redeem(auto_redeemer)
            time.sleep(POLL_INTERVAL_SECONDS)

    now = datetime.now(timezone.utc)
    ref_seconds_left = _round_seconds_left(markets, now)
    round_markets_info = " | ".join(
        f"{asset.upper()}:{m['slug']}" for asset, m in sorted(markets.items())
    )
    log(
        f"{BRIGHT}--- Round {rnd} ---  {CYAN}BTC/ETH 5m scan{RESET}  ({ref_seconds_left:.0f}s left)  {DIM}{round_markets_info}{RESET}"
    )

    # wait until entry window
    now = datetime.now(timezone.utc)
    seconds_left = _round_seconds_left(markets, now)
    if seconds_left > ENTRY_START_SECONDS:
        wait_remaining = seconds_left - ENTRY_START_SECONDS
        log(f"Waiting {wait_remaining:.0f}s until dynamic entry phase...", DIM, level="debug")
        while wait_remaining > 0:
            chunk = min(WAIT_LOG_INTERVAL_SECONDS, wait_remaining)
            time.sleep(chunk)
            wait_remaining -= chunk
            _attempt_auto_redeem(auto_redeemer)

    # dynamic entry
    entry = _attempt_dynamic_entry(client, markets, auto_redeemer, dry_run=dry_run)
    if entry is None:
        stats["total_skipped"] += 1
        log(f"{YELLOW}SKIP:{RESET} No entry in dynamic window (BTC/ETH)")
        _wait_for_resolution(markets, auto_redeemer)
        print()
        return

    selected_asset = entry["asset"]
    market = entry["market"]
    token_id = entry["token_id"]
    buy_price = entry["quoted_price"]
    bet_amount = entry["bet_amount"]

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
        stop_price = entry_price * (1 - STOP_LOSS_PCT)
        slippage_pct = ((entry_price - buy_price) / buy_price) if buy_price > 0 else 0.0

        log(
            f"Entry fill [{selected_asset.upper()}]: {shares:.2f} shares @ ${entry_price:.3f} ({fill['source']}) | SL ${stop_price:.3f}",
            DIM,
            level="debug",
        )

        if slippage_pct >= FILL_SLIPPAGE_WARN_PCT:
            log(
                f"{YELLOW}SLIPPAGE WARNING{RESET}: quote ${buy_price:.3f} -> fill ${entry_price:.3f} ({slippage_pct * 100:.1f}%)",
                YELLOW,
            )

        if (not dry_run) and STOP_LOSS_ENABLED and shares > 0 and entry_price > 0:
            stop_info = _monitor_stop_loss(
                client=client,
                market=market,
                auto_redeemer=auto_redeemer,
                token_id=token_id,
                shares=shares,
                entry_price=entry_price,
            )

    # wait for resolution + redeem
    _wait_for_resolution(markets, auto_redeemer)
    _attempt_auto_redeem(auto_redeemer)

    new_balance = get_balance(client)
    pnl = new_balance - balance

    if dry_run:
        log(f"{DIM}DRY RUN — round complete{RESET} | Balance: ${new_balance:.2f}")
    elif stop_info.get("triggered"):
        stats["total_stop_exits"] += 1
        stats["total_pnl"] += pnl
        color = GREEN if pnl > 0 else RED
        tag = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "EVEN")
        if pnl > 0:
            stats["total_wins"] += 1
            stats["total_stop_wins"] += 1
        elif pnl < 0:
            stats["total_losses"] += 1
            stats["total_stop_losses"] += 1
        log(f"{color}{BRIGHT}{tag} (STOP){RESET}  PnL: {color}{'+'if pnl>=0 else ''}${pnl:.2f}{RESET} | Balance: ${new_balance:.2f}")
    elif pnl > 0:
        stats["total_pnl"] += pnl
        stats["total_wins"] += 1
        log(f"{GREEN}{BRIGHT}WIN{RESET}  PnL: {GREEN}+${pnl:.2f}{RESET} | Balance: ${new_balance:.2f}")
    elif pnl < 0 and pnl > -bet_amount * 0.98:
        stats["total_pnl"] += pnl
        stats["total_losses"] += 1
        log(f"{RED}{BRIGHT}LOSS{RESET} PnL: {RED}-${abs(pnl):.2f}{RESET} | Balance: ${new_balance:.2f}")
    else:
        log(
            f"UNSETTLED  Available balance: ${new_balance:.2f} (redeem pending)",
            YELLOW,
        )
    print()


# ── dynamic entry ─────────────────────────────────────────────────


def _attempt_dynamic_entry(client, markets, auto_redeemer, dry_run=False):
    balance_snapshot = None
    balance_snapshot_ts = 0.0
    blocked_market_slugs = set()

    while True:
        now = datetime.now(timezone.utc)
        seconds_left = _round_seconds_left(markets, now)
        if seconds_left <= 0:
            return None

        params = get_dynamic_entry_params(seconds_left)
        min_odds = params["min_odds"]
        capital_pct = params["capital_pct"]

        token_ids = []
        for m in markets.values():
            token_ids.extend([m["up_token"], m["down_token"]])
        prices = get_token_prices_batch(client, token_ids, side="BUY")

        candidates = []
        for _asset, market in sorted(markets.items()):
            market_slug = market.get("slug") or f"{market.get('asset', '')}:{market.get('condition_id', '')}"
            if market_slug in blocked_market_slugs:
                continue

            candidate = evaluate_candidate_market(market, prices, min_odds=min_odds)
            if candidate:
                if (
                    ENTRY_LOCK_MARKET_ON_HIGH_ODDS_REJECT
                    and not candidate["accepted"]
                    and candidate.get("rejection_reason") == "too_high"
                ):
                    blocked_market_slugs.add(market_slug)
                    log(
                        f"Locking slug for this round (high odds): {market.get('asset', '').upper()} {market.get('slug', '')} @ {candidate['buy_price']:.3f}",
                        YELLOW,
                        level="debug",
                    )
                    continue
                candidates.append(candidate)

        chosen = pick_best_candidate(candidates)
        if chosen is not None:
            now_ts = time.time()
            if balance_snapshot is None or (now_ts - balance_snapshot_ts) >= ENTRY_BALANCE_REFRESH_SECONDS:
                balance_snapshot = get_balance(client)
                balance_snapshot_ts = now_ts

            target_amount = max(MIN_BET_USDC, balance_snapshot * capital_pct)
            bet_amount = math.floor(target_amount * 100) / 100
            if bet_amount > balance_snapshot:
                bet_amount = math.floor(balance_snapshot * 100) / 100
            if bet_amount < MIN_BET_USDC:
                return None

            log(
                f"Entry signal [{seconds_left:.0f}s] {chosen['asset'].upper()}: {chosen['side'].upper()} odds={chosen['buy_price']:.3f} min={min_odds:.3f} | Bet ${bet_amount:.2f} ({capital_pct:.0%})",
                CYAN,
            )
            order_response = place_bet(client, chosen["token_id"], bet_amount, dry_run=dry_run)
            if order_response is not None:
                effective_amount = bet_amount
                if isinstance(order_response, dict):
                    effective_amount = float(order_response.get("_effective_amount", bet_amount))
                return {
                    "asset": chosen["asset"],
                    "market": chosen["market"],
                    "token_id": chosen["token_id"],
                    "side": chosen["side"],
                    "quoted_price": chosen["buy_price"],
                    "bet_amount": effective_amount,
                    "min_odds_at_entry": min_odds,
                    "capital_pct_at_entry": capital_pct,
                    "seconds_left_at_entry": seconds_left,
                    "order_response": order_response,
                }
            log("Order failed, continuing dynamic retries...", YELLOW, level="debug")

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


# ── stop loss ─────────────────────────────────────────────────────


def _monitor_stop_loss(*, client, market, auto_redeemer, token_id, shares, entry_price):
    stop_price = entry_price * (1 - STOP_LOSS_PCT)
    confirmations = 0
    next_sell_attempt_ts = 0.0

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


# ── resolution wait ───────────────────────────────────────────────


def _wait_for_resolution(markets, auto_redeemer):
    if isinstance(markets, dict) and "end_date" in markets:
        end_dates = [markets["end_date"]]
    else:
        end_dates = [m["end_date"] for m in markets.values()]

    now = datetime.now(timezone.utc)
    seconds_left = min((ed - now).total_seconds() for ed in end_dates)
    wait = max(0, seconds_left) + POST_RESOLUTION_BUFFER_SECONDS
    log(f"Waiting {wait:.0f}s for resolution...", DIM, level="debug")
    while wait > 0:
        chunk = min(WAIT_LOG_INTERVAL_SECONDS, wait)
        time.sleep(chunk)
        wait -= chunk
        _attempt_auto_redeem(auto_redeemer)


# ── auto redeem (simple version for CLI) ──────────────────────────


def _attempt_auto_redeem(auto_redeemer, log_unavailable=False, return_result=False):
    redeem = auto_redeemer.redeem_once(max_conditions=AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE)

    if not redeem["attempted"]:
        if log_unavailable and redeem["errors"]:
            log(f"Auto-redeem unavailable: {redeem['errors'][0]}", YELLOW)
        return redeem if return_result else None

    if redeem["claimed"] > 0 or redeem["pending"] > 0 or redeem["errors"]:
        if redeem["claimed"] > 0 or redeem["pending"] > 0:
            log(
                f"Auto-redeem: claimed={redeem['claimed']} pending={redeem['pending']}",
                CYAN if redeem["claimed"] > 0 else YELLOW,
            )
        if redeem["errors"]:
            now_ts = time.time()
            first_error = redeem["errors"][0]
            should_log = (
                first_error != _LAST_REDEEM_ERROR_LOG["message"]
                or now_ts - _LAST_REDEEM_ERROR_LOG["ts"] >= AUTO_REDEEM_ERROR_LOG_COOLDOWN_SECONDS
            )
            if should_log:
                log(f"Auto-redeem detail: {first_error}", DIM, level="debug")
                _LAST_REDEEM_ERROR_LOG["message"] = first_error
                _LAST_REDEEM_ERROR_LOG["ts"] = now_ts
    return redeem if return_result else None


# ── session summary ───────────────────────────────────────────────


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
    time_str = f"{minutes:.0f}m" if minutes < 60 else f"{hours:.1f}h"
    log(f"  Duration:     {time_str}")
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
