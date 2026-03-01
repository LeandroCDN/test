"""
BotManager: runs the trading bot loop in a background thread with
start / stop / pause-entry / force-redeem controls.
"""

import os
import sys
import threading
import time
import math
import re
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from app.services import state_store as store
from app.services.settings_service import load_settings
from app.services.entry_strategy import (
    evaluate_candidate_market,
    pick_best_candidate,
    get_dynamic_entry_params,
)

# ── lazy imports of trading modules ───────────────────────────────

_bot_deps_loaded = False


def _ensure_bot_deps():
    """Add bot source directories to sys.path so bare imports resolve."""
    global _bot_deps_loaded
    if _bot_deps_loaded:
        return
    base = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir))
    for sub in ("", "services", "bot"):
        d = os.path.normpath(os.path.join(base, sub))
        if d not in sys.path:
            sys.path.insert(0, d)
    _bot_deps_loaded = True


class BotManager:
    """Singleton controller for the bot worker thread."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._force_redeem_event = threading.Event()
        self._lock = threading.Lock()
        self._active_settings: dict[str, Any] | None = None
        self._redeem_pending = True
        self._next_redeem_probe_ts = 0.0
        self._next_redeem_attempt_ts = 0.0
        self._redeem_attempt_counter = 0

    # ── public state ──────────────────────────────────────────────

    @property
    def status(self) -> str:
        return store.get_worker_status()

    @property
    def active_settings(self) -> dict[str, Any] | None:
        return dict(self._active_settings) if self._active_settings else None

    # ── start / stop ──────────────────────────────────────────────

    def start(self, dry_run: bool = False) -> tuple[bool, str]:
        with self._lock:
            current = store.get_worker_status()
            if current in ("running", "starting"):
                return False, f"Worker already {current}"
            store.set_worker_status("starting")
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(dry_run,),
                daemon=True,
                name="bot-worker",
            )
            self._thread.start()
        return True, "Worker starting"

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            current = store.get_worker_status()
            if current in ("stopped", "stopping"):
                return False, f"Worker already {current}"
            store.set_worker_status("stopping")
            self._stop_event.set()
        return True, "Stop signal sent"

    def pause_entry(self) -> tuple[bool, str]:
        if store.get_worker_status() != "running":
            return False, "Worker not running"
        store.set_entry_paused(True)
        store.push_event("entry_paused", level="warn")
        return True, "Entry paused"

    def resume_entry(self) -> tuple[bool, str]:
        if not store.is_entry_paused():
            return False, "Entry is not paused"
        store.set_entry_paused(False)
        store.push_event("entry_resumed", level="info")
        return True, "Entry resumed"

    def force_redeem(self) -> tuple[bool, str]:
        if store.get_worker_status() != "running":
            return False, "Worker not running"
        self._force_redeem_event.set()
        store.push_event("force_redeem_requested", level="info")
        return True, "Force-redeem queued"

    # ── should the current loop iteration stop? ───────────────────

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def should_force_redeem(self) -> bool:
        return self._force_redeem_event.is_set()

    def clear_force_redeem(self) -> None:
        self._force_redeem_event.clear()

    # ── config builder ────────────────────────────────────────────

    @staticmethod
    def _build_cfg(runtime_settings: dict, defaults: dict) -> dict:
        """Merge runtime_settings over config.py defaults into a flat dict."""

        def _get(key: str):
            return runtime_settings.get(key, defaults[key])

        return {
            "ENABLED_ASSETS": _get("enabled_assets"),
            "ENTRY_START_SECONDS": _get("entry_start_seconds"),
            "ENTRY_CHECK_INTERVAL_SECONDS": _get("entry_check_interval_seconds"),
            "ENTRY_CHECK_INTERVAL_FAST_SECONDS": _get("entry_check_interval_fast_seconds"),
            "ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS": _get("entry_check_interval_fast_threshold_seconds"),
            "ENTRY_PROFILE_POINTS": _get("entry_profile_points"),
            "ENTRY_BALANCE_REFRESH_SECONDS": _get("entry_balance_refresh_seconds"),
            "MIN_BET_USDC": _get("min_bet_usdc"),
            "POLL_INTERVAL_SECONDS": _get("poll_interval_seconds"),
            "POST_RESOLUTION_BUFFER_SECONDS": _get("post_resolution_buffer_seconds"),
            "MAX_ODDS": _get("max_odds"),
            "WAIT_LOG_INTERVAL_SECONDS": _get("wait_log_interval_seconds"),
            "AUTO_REDEEM_ENABLED": _get("auto_redeem_enabled"),
            "AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE": _get("auto_redeem_max_conditions_per_cycle"),
            "AUTO_REDEEM_ATTEMPT_INTERVAL_SECONDS": _get("auto_redeem_attempt_interval_seconds"),
            "AUTO_REDEEM_PROBE_INTERVAL_SECONDS": _get("auto_redeem_probe_interval_seconds"),
            "AUTO_REDEEM_RATE_LIMIT_BUFFER_SECONDS": _get("auto_redeem_rate_limit_buffer_seconds"),
            "STOP_LOSS_ENABLED": _get("stop_loss_enabled"),
            "STOP_LOSS_PCT": _get("stop_loss_pct"),
            "STOP_LOSS_POLL_SECONDS": _get("stop_loss_poll_seconds"),
            "STOP_LOSS_CONFIRM_TICKS": _get("stop_loss_confirm_ticks"),
            "STOP_LOSS_RETRY_SECONDS": _get("stop_loss_retry_seconds"),
            "FILL_SLIPPAGE_WARN_PCT": _get("fill_slippage_warn_pct"),
            "ENTRY_LOCK_MARKET_ON_HIGH_ODDS_REJECT": _get("entry_lock_market_on_high_odds_reject"),
        }

    # ── main worker loop (runs in background thread) ──────────────

    def _run_loop(self, dry_run: bool) -> None:
        _ensure_bot_deps()
        load_dotenv()

        from config import (
            ENTRY_START_SECONDS, ENTRY_CHECK_INTERVAL_SECONDS,
            ENTRY_CHECK_INTERVAL_FAST_SECONDS, ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS,
            ENTRY_PROFILE_POINTS, ENTRY_BALANCE_REFRESH_SECONDS,
            MIN_BET_USDC, POLL_INTERVAL_SECONDS, POST_RESOLUTION_BUFFER_SECONDS,
            MAX_ODDS, WAIT_LOG_INTERVAL_SECONDS,
            AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE,
            AUTO_REDEEM_ATTEMPT_INTERVAL_SECONDS, AUTO_REDEEM_PROBE_INTERVAL_SECONDS,
            AUTO_REDEEM_RATE_LIMIT_BUFFER_SECONDS,
            STOP_LOSS_ENABLED, STOP_LOSS_PCT, STOP_LOSS_POLL_SECONDS,
            STOP_LOSS_CONFIRM_TICKS, STOP_LOSS_RETRY_SECONDS, FILL_SLIPPAGE_WARN_PCT,
            ENTRY_LOCK_MARKET_ON_HIGH_ODDS_REJECT,
        )
        from market import find_active_crypto_5m_market
        from trader import (
            init_client, get_balance, place_bet,
            get_token_prices_batch, get_token_bid,
            sell_shares, get_entry_fill_details, init_auto_redeemer,
        )

        # store trading functions for use in instance methods
        self._fn = {
            "find_market": find_active_crypto_5m_market,
            "get_balance": get_balance,
            "place_bet": place_bet,
            "get_token_prices_batch": get_token_prices_batch,
            "get_token_bid": get_token_bid,
            "sell_shares": sell_shares,
            "get_entry_fill_details": get_entry_fill_details,
        }

        config_defaults = {
            "enabled_assets": ["btc", "eth"],
            "entry_start_seconds": ENTRY_START_SECONDS,
            "entry_check_interval_seconds": ENTRY_CHECK_INTERVAL_SECONDS,
            "entry_check_interval_fast_seconds": ENTRY_CHECK_INTERVAL_FAST_SECONDS,
            "entry_check_interval_fast_threshold_seconds": ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS,
            "entry_profile_points": ENTRY_PROFILE_POINTS,
            "entry_balance_refresh_seconds": ENTRY_BALANCE_REFRESH_SECONDS,
            "min_bet_usdc": MIN_BET_USDC,
            "poll_interval_seconds": POLL_INTERVAL_SECONDS,
            "post_resolution_buffer_seconds": POST_RESOLUTION_BUFFER_SECONDS,
            "max_odds": MAX_ODDS,
            "wait_log_interval_seconds": WAIT_LOG_INTERVAL_SECONDS,
            "auto_redeem_enabled": True,
            "auto_redeem_max_conditions_per_cycle": AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE,
            "auto_redeem_attempt_interval_seconds": AUTO_REDEEM_ATTEMPT_INTERVAL_SECONDS,
            "auto_redeem_probe_interval_seconds": AUTO_REDEEM_PROBE_INTERVAL_SECONDS,
            "auto_redeem_rate_limit_buffer_seconds": AUTO_REDEEM_RATE_LIMIT_BUFFER_SECONDS,
            "stop_loss_enabled": STOP_LOSS_ENABLED,
            "stop_loss_pct": STOP_LOSS_PCT,
            "stop_loss_poll_seconds": STOP_LOSS_POLL_SECONDS,
            "stop_loss_confirm_ticks": STOP_LOSS_CONFIRM_TICKS,
            "stop_loss_retry_seconds": STOP_LOSS_RETRY_SECONDS,
            "fill_slippage_warn_pct": FILL_SLIPPAGE_WARN_PCT,
            "entry_lock_market_on_high_odds_reject": ENTRY_LOCK_MARKET_ON_HIGH_ODDS_REJECT,
        }

        runtime_settings = load_settings()

        private_key = os.getenv("PK")
        if not private_key:
            store.push_event("error", {"message": "PK not found in .env"}, level="error")
            store.set_worker_status("stopped")
            return

        signature_type = int(os.getenv("SIGNATURE_TYPE", "0"))
        funder = os.getenv("FUNDER")

        try:
            client = init_client(private_key, signature_type=signature_type, funder=funder)
            auto_redeemer = init_auto_redeemer(private_key=private_key, funder=funder)
        except Exception as e:
            store.push_event("error", {"message": f"Init failed: {e}"}, level="error")
            store.set_worker_status("stopped")
            return

        balance = get_balance(client)
        store.update_stats({
            "start_balance": balance,
            "current_balance": balance,
            "total_rounds": 0,
            "total_entries": 0,
            "total_btc_entries": 0,
            "total_eth_entries": 0,
            "total_wins": 0,
            "total_losses": 0,
            "total_unsettled": 0,
            "total_stop_exits": 0,
            "total_stop_wins": 0,
            "total_stop_losses": 0,
            "total_skipped": 0,
            "total_pnl": 0.0,
        })
        store.set_worker_status("running")
        store.push_event("worker_started", {"balance": balance, "dry_run": dry_run})
        self._active_settings = dict(runtime_settings)
        self._redeem_pending = True
        self._next_redeem_probe_ts = 0.0
        self._next_redeem_attempt_ts = 0.0
        self._redeem_attempt_counter = 0

        cfg = self._build_cfg(runtime_settings, config_defaults)

        try:
            while not self._stop_event.is_set():
                try:
                    self._run_single_round(
                        client=client,
                        auto_redeemer=auto_redeemer,
                        dry_run=dry_run,
                        cfg=cfg,
                    )
                except Exception as e:
                    store.push_event("round_error", {"message": str(e)}, level="error")
                    self._interruptible_sleep(10)
        finally:
            new_bal = get_balance(client)
            store.update_stats({"current_balance": new_bal})
            store.push_event("worker_stopped", {"balance": new_bal})
            store.set_worker_status("stopped")
            self._active_settings = None
            self._redeem_pending = True
            self._next_redeem_probe_ts = 0.0
            self._next_redeem_attempt_ts = 0.0
            self._fn = {}

    # ── single round ──────────────────────────────────────────────

    def _run_single_round(self, *, client, auto_redeemer, dry_run, cfg):
        get_balance = self._fn["get_balance"]
        get_entry_fill_details = self._fn["get_entry_fill_details"]

        stats = store.get_stats()
        round_num = stats["total_rounds"] + 1
        store.update_stats({"total_rounds": round_num})
        balance = get_balance(client)
        store.update_stats({"current_balance": balance})

        self._try_redeem(auto_redeemer, cfg, reason="round_start")
        self._handle_force_redeem(auto_redeemer, cfg)

        # discover markets
        markets = None
        while markets is None and not self._stop_event.is_set():
            markets = self._get_active_round_markets(cfg.get("ENABLED_ASSETS", ["btc", "eth"]))
            if markets is None:
                self._try_redeem(auto_redeemer, cfg, reason="market_discovery")
                self._interruptible_sleep(cfg["POLL_INTERVAL_SECONDS"])

        if self._stop_event.is_set():
            return

        now = datetime.now(timezone.utc)
        seconds_left = self._round_seconds_left(markets, now)
        assets_found = sorted(markets.keys())
        store.set_current_round({
            "round": round_num,
            "assets": assets_found,
            "seconds_left": round(seconds_left, 1),
        })
        store.push_event("round_started", {
            "round": round_num,
            "assets": assets_found,
            "seconds_left": round(seconds_left, 1),
        })

        # wait until entry window
        self._wait_until_entry_window(markets, auto_redeemer, cfg)

        if self._stop_event.is_set():
            return

        # entry paused?
        if store.is_entry_paused():
            store.update_stats({"total_skipped": store.get_stats()["total_skipped"] + 1})
            store.push_event("round_skipped", {"reason": "Entry paused by user"})
            self._wait_for_resolution(markets, auto_redeemer, cfg)
            store.set_current_round(None)
            return

        # dynamic entry
        entry = self._attempt_dynamic_entry(client, markets, auto_redeemer, cfg, dry_run)
        if entry is None:
            store.update_stats({"total_skipped": store.get_stats()["total_skipped"] + 1})
            store.push_event("round_skipped", {"reason": "No entry in dynamic window"})
            self._wait_for_resolution(markets, auto_redeemer, cfg)
            store.set_current_round(None)
            return

        # post entry
        selected_asset = entry["asset"]
        token_id = entry["token_id"]
        buy_price = entry["quoted_price"]
        bet_amount = entry["bet_amount"]

        s = store.get_stats()
        s["total_entries"] += 1
        if selected_asset == "btc":
            s["total_btc_entries"] += 1
        elif selected_asset == "eth":
            s["total_eth_entries"] += 1
        store.update_stats(s)

        store.push_event("entry_sent", {
            "asset": selected_asset,
            "side": entry.get("side", ""),
            "price": buy_price,
            "amount": bet_amount,
            "seconds_left": round(entry.get("seconds_left_at_entry", 0), 1),
        })

        result = entry["order_response"]
        stop_info = {"triggered": False}

        if result is not None:
            fill = get_entry_fill_details(
                client=client,
                order_response=result,
                token_id=token_id,
                fallback_price=buy_price,
                fallback_amount=bet_amount,
            )
            entry_price = fill["entry_price"]
            shares = fill["shares"]
            stop_price = entry_price * (1 - cfg["STOP_LOSS_PCT"])

            store.push_event("fill_received", {
                "asset": selected_asset,
                "entry_price": round(entry_price, 4),
                "shares": round(shares, 4),
                "source": fill["source"],
                "stop_price": round(stop_price, 4),
            })

            slippage_pct = ((entry_price - buy_price) / buy_price) if buy_price > 0 else 0.0
            if slippage_pct >= cfg["FILL_SLIPPAGE_WARN_PCT"]:
                store.push_event("slippage_warning", {
                    "quoted": round(buy_price, 4),
                    "fill": round(entry_price, 4),
                    "pct": round(slippage_pct * 100, 2),
                }, level="warn")

            if (not dry_run) and cfg["STOP_LOSS_ENABLED"] and shares > 0 and entry_price > 0:
                stop_info = self._monitor_stop_loss(
                    client=client,
                    market=entry["market"],
                    auto_redeemer=auto_redeemer,
                    token_id=token_id,
                    shares=shares,
                    entry_price=entry_price,
                    cfg=cfg,
                )

        # wait for resolution
        self._wait_for_resolution(markets, auto_redeemer, cfg)
        self._try_redeem(auto_redeemer, cfg, reason="post_resolution")

        new_balance = get_balance(client)
        pnl = new_balance - balance
        store.update_stats({"current_balance": new_balance})

        redeemed = pnl > -bet_amount * 0.98
        if stop_info.get("triggered"):
            redeemed = True

        outcome = self._classify_outcome(pnl, stop_info, dry_run, redeemed)
        s = store.get_stats()
        if outcome != "unsettled":
            s["total_pnl"] += pnl
        if outcome == "win":
            s["total_wins"] += 1
        elif outcome == "loss":
            s["total_losses"] += 1
        elif outcome == "unsettled":
            s["total_unsettled"] += 1
        if stop_info.get("triggered"):
            s["total_stop_exits"] += 1
            if pnl > 0:
                s["total_stop_wins"] += 1
            elif pnl < 0:
                s["total_stop_losses"] += 1
        store.update_stats(s)

        store.push_event("round_result", {
            "round": round_num,
            "outcome": outcome,
            "pnl": round(pnl, 4) if outcome != "unsettled" else None,
            "balance": round(new_balance, 2),
            "stop_triggered": stop_info.get("triggered", False),
        })
        store.set_current_round(None)

    # ── helpers ───────────────────────────────────────────────────

    def _get_active_round_markets(self, enabled_assets):
        find_market = self._fn["find_market"]
        found = {}
        for asset in enabled_assets:
            market = find_market(asset)
            if market is not None:
                found[asset] = market
        return found or None

    @staticmethod
    def _round_seconds_left(markets, now=None):
        if now is None:
            now = datetime.now(timezone.utc)
        return min((m["end_date"] - now).total_seconds() for m in markets.values())

    def _interruptible_sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self._stop_event.is_set():
            time.sleep(min(0.5, end - time.time()))

    def _handle_force_redeem(self, auto_redeemer, cfg):
        if self.should_force_redeem():
            self._try_redeem(auto_redeemer, cfg, force=True, reason="force_redeem")
            self.clear_force_redeem()

    def _wait_until_entry_window(self, markets, auto_redeemer, cfg):
        now = datetime.now(timezone.utc)
        seconds_left = self._round_seconds_left(markets, now)
        if seconds_left <= cfg["ENTRY_START_SECONDS"]:
            return
        wait_remaining = seconds_left - cfg["ENTRY_START_SECONDS"]
        while wait_remaining > 0 and not self._stop_event.is_set():
            chunk = min(cfg["WAIT_LOG_INTERVAL_SECONDS"], wait_remaining)
            self._interruptible_sleep(chunk)
            wait_remaining -= chunk
            self._try_redeem(auto_redeemer, cfg, reason="pre_entry_wait")
            self._handle_force_redeem(auto_redeemer, cfg)

    # ── redeem scheduler ──────────────────────────────────────────

    def _try_redeem(self, auto_redeemer, cfg, force=False, reason="loop"):
        if not cfg.get("AUTO_REDEEM_ENABLED", True):
            return {"attempted": False, "claimed": 0, "pending": 0, "errors": []}

        now = time.time()
        attempt_interval = max(1, int(cfg.get("AUTO_REDEEM_ATTEMPT_INTERVAL_SECONDS", 30)))
        probe_interval = max(1, int(cfg.get("AUTO_REDEEM_PROBE_INTERVAL_SECONDS", 30)))
        rate_limit_buffer = max(0, int(cfg.get("AUTO_REDEEM_RATE_LIMIT_BUFFER_SECONDS", 5)))

        if force:
            self._redeem_pending = True
            self._next_redeem_attempt_ts = 0.0
            self._next_redeem_probe_ts = 0.0

        if not self._redeem_pending:
            if now < self._next_redeem_probe_ts:
                return {"attempted": False, "claimed": 0, "pending": 0, "errors": []}
            pending_conditions = auto_redeemer.peek_redeemable_conditions(limit=5)
            if not pending_conditions:
                self._next_redeem_probe_ts = now + probe_interval
                return {"attempted": False, "claimed": 0, "pending": 0, "errors": []}
            self._redeem_pending = True
            store.push_event(
                "redeem_pending_detected",
                {"count": len(pending_conditions), "reason": reason},
            )

        if (not force) and now < self._next_redeem_attempt_ts:
            return {"attempted": False, "claimed": 0, "pending": 0, "errors": []}

        self._redeem_attempt_counter += 1
        max_conds = cfg["AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE"]
        if force:
            max_conds = 10

        store.push_event("redeem_attempt", {
            "attempt": self._redeem_attempt_counter,
            "reason": reason,
            "max_conditions": max_conds,
            "force": force,
        })

        redeem = auto_redeemer.redeem_once(max_conditions=max_conds)
        claimed = int(redeem.get("claimed", 0) or 0)
        relay_pending = int(redeem.get("pending", 0) or 0)
        errors = redeem.get("errors") or []

        if claimed > 0:
            store.push_event("redeem_claimed", {
                "claimed": claimed,
                "pending": relay_pending,
                "attempt": self._redeem_attempt_counter,
            })

        if errors:
            store.push_event("redeem_error", {
                "errors": errors[:3],
                "attempt": self._redeem_attempt_counter,
            }, level="warn")

        reset_seconds = self._extract_rate_limit_reset_seconds(errors)
        if reset_seconds is not None:
            wait_s = max(1, int(reset_seconds) + rate_limit_buffer)
            self._redeem_pending = True
            self._next_redeem_attempt_ts = now + wait_s
            store.push_event("redeem_rate_limited", {
                "retry_in_seconds": wait_s,
                "attempt": self._redeem_attempt_counter,
            }, level="warn")
            return redeem

        if errors:
            self._redeem_pending = True
            self._next_redeem_attempt_ts = now + attempt_interval
            return redeem

        if claimed > 0 or relay_pending > 0:
            self._redeem_pending = True
            self._next_redeem_attempt_ts = now + attempt_interval
            return redeem

        self._redeem_pending = False
        self._next_redeem_probe_ts = now + probe_interval
        self._next_redeem_attempt_ts = 0.0
        return redeem

    @staticmethod
    def _extract_rate_limit_reset_seconds(errors):
        if not errors:
            return None
        best = None
        for err in errors:
            text = str(err).lower()
            m = re.search(r"resets?\s+in\s+(\d+)\s+seconds?", text)
            if m:
                sec = int(m.group(1))
                best = sec if best is None else max(best, sec)
        return best

    # ── dynamic entry ─────────────────────────────────────────────

    def _attempt_dynamic_entry(self, client, markets, auto_redeemer, cfg, dry_run):
        get_balance = self._fn["get_balance"]
        place_bet = self._fn["place_bet"]
        get_token_prices_batch = self._fn["get_token_prices_batch"]

        balance_snapshot = None
        balance_snapshot_ts = 0.0
        blocked_market_slugs = set()

        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            seconds_left = self._round_seconds_left(markets, now)
            if seconds_left <= 0:
                return None

            params = get_dynamic_entry_params(seconds_left, cfg["ENTRY_PROFILE_POINTS"])
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
                candidate = evaluate_candidate_market(
                    market, prices, min_odds=min_odds, max_odds=cfg["MAX_ODDS"],
                )
                if candidate:
                    if (
                        cfg.get("ENTRY_LOCK_MARKET_ON_HIGH_ODDS_REJECT", True)
                        and not candidate["accepted"]
                        and candidate.get("rejection_reason") == "too_high"
                    ):
                        blocked_market_slugs.add(market_slug)
                        store.push_event(
                            "entry_slug_locked_high_odds",
                            {
                                "asset": market.get("asset"),
                                "slug": market.get("slug"),
                                "price": round(candidate["buy_price"], 4),
                                "max_odds": round(cfg["MAX_ODDS"], 4),
                            },
                            level="warn",
                        )
                        continue
                    candidates.append(candidate)

            chosen = pick_best_candidate(candidates)
            if chosen is not None:
                now_ts = time.time()
                if balance_snapshot is None or (now_ts - balance_snapshot_ts) >= cfg["ENTRY_BALANCE_REFRESH_SECONDS"]:
                    balance_snapshot = get_balance(client)
                    balance_snapshot_ts = now_ts

                target_amount = max(cfg["MIN_BET_USDC"], balance_snapshot * capital_pct)
                bet_amount = math.floor(target_amount * 100) / 100
                if bet_amount > balance_snapshot:
                    bet_amount = math.floor(balance_snapshot * 100) / 100
                if bet_amount < cfg["MIN_BET_USDC"]:
                    return None

                order_response = place_bet(client, chosen["token_id"], bet_amount, dry_run=dry_run)
                if order_response is not None:
                    self._redeem_pending = True
                    self._next_redeem_probe_ts = 0.0
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

            redeem = self._try_redeem(auto_redeemer, cfg, reason="entry_loop")
            if redeem and redeem.get("claimed", 0) > 0:
                balance_snapshot = get_balance(client)
                balance_snapshot_ts = time.time()

            sleep_s = (
                cfg["ENTRY_CHECK_INTERVAL_FAST_SECONDS"]
                if seconds_left <= cfg["ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS"]
                else cfg["ENTRY_CHECK_INTERVAL_SECONDS"]
            )
            self._interruptible_sleep(sleep_s)

        return None

    # ── stop loss ─────────────────────────────────────────────────

    def _monitor_stop_loss(self, *, client, market, auto_redeemer, token_id, shares, entry_price, cfg):
        get_token_bid = self._fn["get_token_bid"]
        sell_shares = self._fn["sell_shares"]

        stop_price = entry_price * (1 - cfg["STOP_LOSS_PCT"])
        confirmations = 0
        next_sell_attempt_ts = 0.0

        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            seconds_left = (market["end_date"] - now).total_seconds()
            if seconds_left <= 0:
                return {"triggered": False}

            bid = get_token_bid(client, token_id)
            if bid is not None and bid <= stop_price:
                confirmations += 1
            else:
                confirmations = 0

            if confirmations >= cfg["STOP_LOSS_CONFIRM_TICKS"] and time.time() >= next_sell_attempt_ts:
                store.push_event("stop_triggered", {
                    "bid": round(bid, 4) if bid else None,
                    "stop_price": round(stop_price, 4),
                    "shares": round(shares, 4),
                }, level="warn")

                sell_result = sell_shares(client, token_id, shares, dry_run=False)
                if sell_result is not None:
                    return {"triggered": True, "exit_bid": bid, "stop_price": stop_price}
                next_sell_attempt_ts = time.time() + cfg["STOP_LOSS_RETRY_SECONDS"]
                confirmations = 0

            self._try_redeem(auto_redeemer, cfg, reason="stop_loss_monitor")
            self._handle_force_redeem(auto_redeemer, cfg)
            self._interruptible_sleep(cfg["STOP_LOSS_POLL_SECONDS"])

        return {"triggered": False}

    # ── resolution wait ───────────────────────────────────────────

    def _wait_for_resolution(self, markets, auto_redeemer, cfg):
        now = datetime.now(timezone.utc)
        end_dates = [m["end_date"] for m in markets.values()]
        seconds_left = min((ed - now).total_seconds() for ed in end_dates)
        wait = max(0, seconds_left) + cfg["POST_RESOLUTION_BUFFER_SECONDS"]
        while wait > 0 and not self._stop_event.is_set():
            chunk = min(cfg["WAIT_LOG_INTERVAL_SECONDS"], wait)
            self._interruptible_sleep(chunk)
            wait -= chunk
            self._try_redeem(auto_redeemer, cfg, reason="wait_resolution")
            self._handle_force_redeem(auto_redeemer, cfg)

    # ── outcome classifier ────────────────────────────────────────

    @staticmethod
    def _classify_outcome(pnl, stop_info, dry_run, redeemed=True):
        if dry_run:
            return "dry_run"
        if not redeemed:
            return "unsettled"
        if pnl > 0:
            return "win"
        if pnl < 0:
            return "loss"
        return "even"


bot_manager = BotManager()
