"""
Isolated manager for the second BTC-only bot.
"""

from __future__ import annotations

import math
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from app.bots.bot2 import config as bot2_cfg
from app.bots.bot2 import state_store as store
from app.bots.bot2.settings_service import load_settings
from app.bots.bot2.strategy import estimate_up_probability, evaluate_trade_setup, fetch_reference_snapshot
from app.services.entry_strategy import get_dynamic_entry_params
from app.services.volatility import build_snapshot_from_candles, fetch_candles

_shared_deps_loaded = False


def _ensure_shared_deps() -> None:
    global _shared_deps_loaded
    if _shared_deps_loaded:
        return
    base = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
    for sub in ("", "services", "bot"):
        path = os.path.normpath(os.path.join(base, sub))
        if path not in sys.path:
            sys.path.insert(0, path)
    _shared_deps_loaded = True


class Bot2Manager:
    """Controls the second bot worker thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._redeem_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._force_redeem_event = threading.Event()
        self._redeem_wakeup_event = threading.Event()
        self._redeem_request_lock = threading.Lock()
        self._redeem_requested = False
        self._redeem_force_requested = False
        self._redeem_reason = "loop"
        self._lock = threading.Lock()
        self._active_settings: dict[str, Any] | None = None
        self._redeem_pending = True
        self._next_redeem_probe_ts = 0.0
        self._next_redeem_attempt_ts = 0.0
        self._redeem_attempt_counter = 0
        self._fn: dict[str, Any] = {}

    @property
    def status(self) -> str:
        return store.get_worker_status()

    @property
    def active_settings(self) -> dict[str, Any] | None:
        return dict(self._active_settings) if self._active_settings else None

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
                name="bot2-worker",
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
        store.push_event("entry_resumed")
        return True, "Entry resumed"

    def force_redeem(self) -> tuple[bool, str]:
        if store.get_worker_status() != "running":
            return False, "Worker not running"
        self._request_redeem(reason="force_redeem", force=True)
        store.push_event("force_redeem_requested")
        return True, "Force-redeem queued"

    def should_force_redeem(self) -> bool:
        return self._force_redeem_event.is_set()

    def clear_force_redeem(self) -> None:
        self._force_redeem_event.clear()

    def _request_redeem(self, *, reason: str, force: bool = False) -> None:
        with self._redeem_request_lock:
            self._redeem_requested = True
            self._redeem_reason = reason
            if force:
                self._redeem_force_requested = True
                self._force_redeem_event.set()
        self._redeem_wakeup_event.set()

    def _redeem_loop(self, auto_redeemer, cfg: dict[str, Any]) -> None:
        while not self._stop_event.is_set():
            self._redeem_wakeup_event.wait(timeout=1.0)
            self._redeem_wakeup_event.clear()

            force = False
            reason = "redeem_bg"
            requested = False
            with self._redeem_request_lock:
                if self._redeem_requested:
                    requested = True
                    reason = self._redeem_reason
                    self._redeem_requested = False
                if self._redeem_force_requested:
                    force = True
                    self._redeem_force_requested = False
                    self._force_redeem_event.clear()

            # Keep auto-redeem cadence alive even without explicit requests.
            if (not requested) and (not force) and (not self._redeem_pending):
                continue

            try:
                self._try_redeem(
                    auto_redeemer,
                    cfg,
                    force=force,
                    reason=("force_redeem" if force else reason),
                )
            except Exception as exc:
                store.push_event("redeem_error", {"errors": [str(exc)]}, level="warn")

    @staticmethod
    def _build_cfg(runtime_settings: dict[str, Any]) -> dict[str, Any]:
        return {
            "ENABLED_ASSETS": runtime_settings.get("enabled_assets", list(bot2_cfg.ENABLED_ASSETS)),
            "ASSET_PRIORITY": list(bot2_cfg.ASSET_PRIORITY),
            "ENTRY_START_SECONDS": runtime_settings.get("entry_start_seconds", bot2_cfg.ENTRY_START_SECONDS),
            "LIVE_MONITOR_START_SECONDS": runtime_settings.get(
                "live_monitor_start_seconds",
                bot2_cfg.LIVE_MONITOR_START_SECONDS,
            ),
            "ENTRY_CHECK_INTERVAL_SECONDS": runtime_settings.get(
                "entry_check_interval_seconds",
                bot2_cfg.ENTRY_CHECK_INTERVAL_SECONDS,
            ),
            "ENTRY_CHECK_INTERVAL_FAST_SECONDS": runtime_settings.get(
                "entry_check_interval_fast_seconds",
                bot2_cfg.ENTRY_CHECK_INTERVAL_FAST_SECONDS,
            ),
            "ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS": runtime_settings.get(
                "entry_check_interval_fast_threshold_seconds",
                bot2_cfg.ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS,
            ),
            "ENTRY_LIMIT_FLOATING_ENABLED": runtime_settings.get(
                "entry_limit_floating_enabled",
                bot2_cfg.ENTRY_LIMIT_FLOATING_ENABLED,
            ),
            "ENTRY_LIMIT_PHASE_RATIO": runtime_settings.get(
                "entry_limit_phase_ratio",
                bot2_cfg.ENTRY_LIMIT_PHASE_RATIO,
            ),
            "ENTRY_LIMIT_REPRICE_INTERVAL_SECONDS": runtime_settings.get(
                "entry_limit_reprice_interval_seconds",
                bot2_cfg.ENTRY_LIMIT_REPRICE_INTERVAL_SECONDS,
            ),
            "ENTRY_LIMIT_MAX_REPRICES": runtime_settings.get(
                "entry_limit_max_reprices",
                bot2_cfg.ENTRY_LIMIT_MAX_REPRICES,
            ),
            "ENTRY_PROFILE_POINTS": runtime_settings.get("entry_profile_points", bot2_cfg.ENTRY_PROFILE_POINTS),
            "ENTRY_BALANCE_REFRESH_SECONDS": runtime_settings.get(
                "entry_balance_refresh_seconds",
                bot2_cfg.ENTRY_BALANCE_REFRESH_SECONDS,
            ),
            "MIN_BET_USDC": runtime_settings.get("min_bet_usdc", bot2_cfg.MIN_BET_USDC),
            "BET_SIZING_MODE": runtime_settings.get("bet_sizing_mode", bot2_cfg.BET_SIZING_MODE),
            "MAX_ODDS": runtime_settings.get("max_odds", bot2_cfg.MAX_ODDS),
            "FILL_SLIPPAGE_WARN_PCT": runtime_settings.get(
                "fill_slippage_warn_pct",
                bot2_cfg.FILL_SLIPPAGE_WARN_PCT,
            ),
            "POLL_INTERVAL_SECONDS": runtime_settings.get(
                "poll_interval_seconds",
                bot2_cfg.POLL_INTERVAL_SECONDS,
            ),
            "POST_RESOLUTION_BUFFER_SECONDS": runtime_settings.get(
                "post_resolution_buffer_seconds",
                bot2_cfg.POST_RESOLUTION_BUFFER_SECONDS,
            ),
            "WAIT_LOG_INTERVAL_SECONDS": runtime_settings.get(
                "wait_log_interval_seconds",
                bot2_cfg.WAIT_LOG_INTERVAL_SECONDS,
            ),
            "AUTO_REDEEM_ENABLED": runtime_settings.get(
                "auto_redeem_enabled",
                bot2_cfg.AUTO_REDEEM_ENABLED,
            ),
            "AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE": runtime_settings.get(
                "auto_redeem_max_conditions_per_cycle",
                bot2_cfg.AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE,
            ),
            "AUTO_REDEEM_ATTEMPT_INTERVAL_SECONDS": runtime_settings.get(
                "auto_redeem_attempt_interval_seconds",
                bot2_cfg.AUTO_REDEEM_ATTEMPT_INTERVAL_SECONDS,
            ),
            "AUTO_REDEEM_PROBE_INTERVAL_SECONDS": runtime_settings.get(
                "auto_redeem_probe_interval_seconds",
                bot2_cfg.AUTO_REDEEM_PROBE_INTERVAL_SECONDS,
            ),
            "AUTO_REDEEM_RATE_LIMIT_BUFFER_SECONDS": runtime_settings.get(
                "auto_redeem_rate_limit_buffer_seconds",
                bot2_cfg.AUTO_REDEEM_RATE_LIMIT_BUFFER_SECONDS,
            ),
            "VOLATILITY_FILTER_ENABLED": runtime_settings.get(
                "volatility_filter_enabled",
                bot2_cfg.VOLATILITY_FILTER_ENABLED,
            ),
            "VOLATILITY_REFRESH_SECONDS": runtime_settings.get(
                "volatility_refresh_seconds",
                bot2_cfg.VOLATILITY_REFRESH_SECONDS,
            ),
            "VOLATILITY_INTERVAL": runtime_settings.get(
                "volatility_interval",
                bot2_cfg.VOLATILITY_INTERVAL,
            ),
            "VOLATILITY_LOOKBACK_CANDLES": runtime_settings.get(
                "volatility_lookback_candles",
                bot2_cfg.VOLATILITY_LOOKBACK_CANDLES,
            ),
            "VOLATILITY_LOW_THRESHOLD": runtime_settings.get(
                "volatility_low_threshold",
                bot2_cfg.VOLATILITY_LOW_THRESHOLD,
            ),
            "VOLATILITY_HIGH_THRESHOLD": runtime_settings.get(
                "volatility_high_threshold",
                bot2_cfg.VOLATILITY_HIGH_THRESHOLD,
            ),
            "VOLATILITY_EXTREME_THRESHOLD": runtime_settings.get(
                "volatility_extreme_threshold",
                bot2_cfg.VOLATILITY_EXTREME_THRESHOLD,
            ),
            "VOLATILITY_MIN_ODDS_BUMP_HIGH": runtime_settings.get(
                "volatility_min_odds_bump_high",
                bot2_cfg.VOLATILITY_MIN_ODDS_BUMP_HIGH,
            ),
            "VOLATILITY_MIN_ODDS_BUMP_EXTREME": runtime_settings.get(
                "volatility_min_odds_bump_extreme",
                bot2_cfg.VOLATILITY_MIN_ODDS_BUMP_EXTREME,
            ),
            "VOLATILITY_CAPITAL_MULT_LOW": runtime_settings.get(
                "volatility_capital_mult_low",
                bot2_cfg.VOLATILITY_CAPITAL_MULT_LOW,
            ),
            "VOLATILITY_CAPITAL_MULT_HIGH": runtime_settings.get(
                "volatility_capital_mult_high",
                bot2_cfg.VOLATILITY_CAPITAL_MULT_HIGH,
            ),
            "VOLATILITY_CAPITAL_MULT_EXTREME": runtime_settings.get(
                "volatility_capital_mult_extreme",
                bot2_cfg.VOLATILITY_CAPITAL_MULT_EXTREME,
            ),
            "FAIR_VALUE_SIGMA_FLOOR_PCT": runtime_settings.get(
                "fair_value_sigma_floor_pct",
                bot2_cfg.FAIR_VALUE_SIGMA_FLOOR_PCT,
            ),
            "FAIR_VALUE_NO_TRADE_BAND_PCT": runtime_settings.get(
                "fair_value_no_trade_band_pct",
                bot2_cfg.FAIR_VALUE_NO_TRADE_BAND_PCT,
            ),
            "FAIR_VALUE_MAX_SPREAD": runtime_settings.get(
                "fair_value_max_spread",
                bot2_cfg.FAIR_VALUE_MAX_SPREAD,
            ),
            "FAIR_VALUE_REQUOTE_THRESHOLD": runtime_settings.get(
                "fair_value_requote_threshold",
                bot2_cfg.FAIR_VALUE_REQUOTE_THRESHOLD,
            ),
            "FAIR_VALUE_AGGRESSIVE_EDGE": runtime_settings.get(
                "fair_value_aggressive_edge",
                bot2_cfg.FAIR_VALUE_AGGRESSIVE_EDGE,
            ),
            "FAIR_VALUE_MIN_MODEL_PROBABILITY": runtime_settings.get(
                "fair_value_min_model_probability",
                bot2_cfg.FAIR_VALUE_MIN_MODEL_PROBABILITY,
            ),
            "FAIR_VALUE_MIN_MARKET_PROBABILITY": runtime_settings.get(
                "fair_value_min_market_probability",
                bot2_cfg.FAIR_VALUE_MIN_MARKET_PROBABILITY,
            ),
            "IGNORE_EDGE_FILTER": runtime_settings.get(
                "ignore_edge_filter",
                bot2_cfg.IGNORE_EDGE_FILTER,
            ),
            "CERTAINTY_SECONDS_THRESHOLD": runtime_settings.get(
                "certainty_seconds_threshold",
                bot2_cfg.CERTAINTY_SECONDS_THRESHOLD,
            ),
            "CERTAINTY_AVG_THRESHOLD": runtime_settings.get(
                "certainty_avg_threshold",
                bot2_cfg.CERTAINTY_AVG_THRESHOLD,
            ),
            "ROLLING_WINDOW_SECONDS": runtime_settings.get(
                "rolling_window_seconds",
                bot2_cfg.ROLLING_WINDOW_SECONDS,
            ),
        }

    def _run_loop(self, dry_run: bool) -> None:
        _ensure_shared_deps()
        load_dotenv()

        from market import find_active_crypto_5m_market
        from trader import (
            get_balance,
            get_entry_fill_details,
            get_token_prices_batch,
            init_auto_redeemer,
            init_client,
            place_limit_sell,
            place_bet,
        )

        self._fn = {
            "find_market": find_active_crypto_5m_market,
            "get_balance": get_balance,
            "get_entry_fill_details": get_entry_fill_details,
            "get_token_prices_batch": get_token_prices_batch,
            "init_auto_redeemer": init_auto_redeemer,
            "init_client": init_client,
            "place_limit_sell": place_limit_sell,
            "place_bet": place_bet,
        }

        runtime_settings = load_settings()
        cfg = self._build_cfg(runtime_settings)

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
        except Exception as exc:
            store.push_event("error", {"message": f"Init failed: {exc}"}, level="error")
            store.set_worker_status("stopped")
            return

        balance = get_balance(client)
        store.update_stats(
            {
                "start_balance": balance,
                "current_balance": balance,
                "total_rounds": 0,
                "total_entries": 0,
                "total_btc_entries": 0,
                "total_eth_entries": 0,
                "total_sol_entries": 0,
                "total_pnl": 0.0,
            }
        )
        store.set_worker_status("running")
        store.push_event("worker_started", {"balance": balance, "dry_run": dry_run})
        self._active_settings = dict(runtime_settings)
        self._redeem_pending = True
        self._next_redeem_probe_ts = 0.0
        self._next_redeem_attempt_ts = 0.0
        self._redeem_attempt_counter = 0
        self._redeem_requested = False
        self._redeem_force_requested = False
        self._redeem_reason = "worker_start"
        self._redeem_wakeup_event.clear()
        self._force_redeem_event.clear()
        self._redeem_thread = threading.Thread(
            target=self._redeem_loop,
            args=(auto_redeemer, cfg),
            daemon=True,
            name="bot2-redeem-worker",
        )
        self._redeem_thread.start()
        self._request_redeem(reason="worker_start", force=False)

        try:
            while not self._stop_event.is_set():
                try:
                    self._run_single_round(client=client, auto_redeemer=auto_redeemer, dry_run=dry_run, cfg=cfg)
                except Exception as exc:
                    store.push_event("round_error", {"message": str(exc)}, level="error")
                    self._interruptible_sleep(5)
        finally:
            self._redeem_wakeup_event.set()
            if self._redeem_thread and self._redeem_thread.is_alive():
                self._redeem_thread.join(timeout=2.0)
            try:
                new_balance = get_balance(client)
            except Exception:
                new_balance = 0.0
            store.update_stats({"current_balance": new_balance})
            store.push_event("worker_stopped", {"balance": new_balance})
            store.set_worker_status("stopped")
            store.set_current_round(None)
            store.set_latest_evaluation(None)
            store.clear_eval_history()
            self._active_settings = None
            self._redeem_pending = True
            self._next_redeem_probe_ts = 0.0
            self._next_redeem_attempt_ts = 0.0
            self._redeem_thread = None
            self._fn = {}

    def _run_single_round(self, *, client, auto_redeemer, dry_run: bool, cfg: dict[str, Any]) -> None:
        get_balance = self._fn["get_balance"]
        get_entry_fill_details = self._fn["get_entry_fill_details"]

        stats = store.get_stats()
        round_num = stats["total_rounds"] + 1
        store.update_stats({"total_rounds": round_num, "current_balance": get_balance(client)})

        self._request_redeem(reason="round_start")

        enabled_assets = self._ordered_assets(cfg["ENABLED_ASSETS"], cfg["ASSET_PRIORITY"])
        markets: dict[str, Any] = {}
        while not markets and not self._stop_event.is_set():
            markets = self._discover_markets(enabled_assets)
            if not markets:
                self._request_redeem(reason="market_discovery")
                self._interruptible_sleep(cfg["POLL_INTERVAL_SECONDS"])

        if self._stop_event.is_set() or not markets:
            return

        seconds_left = self._seconds_left_for_markets(markets)
        asset_list = list(markets.keys())
        store.set_current_round({"round": round_num, "assets": asset_list, "seconds_left": round(seconds_left, 1)})
        store.set_latest_evaluation(None)
        store.push_event(
            "round_started",
            {"round": round_num, "assets": asset_list, "seconds_left": round(seconds_left, 1)},
        )

        self._wait_until_entry_window(markets, auto_redeemer, cfg)
        if self._stop_event.is_set():
            return

        if store.is_entry_paused():
            store.push_event("round_skipped", {"reason": "Entry paused by user"})
            self._wait_for_resolution(markets, auto_redeemer, cfg)
            store.set_current_round(None)
            store.set_latest_evaluation(None)
            return

        entry = self._attempt_fair_value_entry(client, markets, auto_redeemer, cfg, dry_run)
        if entry is None:
            store.push_event("round_skipped", {"reason": "No fair-value entry in window"})
            self._wait_for_resolution(markets, auto_redeemer, cfg)
            store.set_current_round(None)
            store.set_latest_evaluation(None)
            return

        stats = store.get_stats()
        stats["total_entries"] += 1
        stats[self._stats_key_for_asset(entry["asset"])] = stats.get(self._stats_key_for_asset(entry["asset"]), 0) + 1
        store.update_stats(stats)

        store.push_event(
            "entry_sent",
            {
                "asset": entry["asset"],
                "side": entry["side"],
                "price": entry["quoted_price"],
                "amount": entry["bet_amount"],
                "seconds_left": round(entry["seconds_left_at_entry"], 1),
            },
        )

        result = entry["order_response"]
        if result is not None:
            fill = get_entry_fill_details(
                client=client,
                order_response=result,
                token_id=entry["token_id"],
                fallback_price=entry["quoted_price"],
                fallback_amount=entry["bet_amount"],
            )
            entry_price = fill["entry_price"]
            shares = fill["shares"]
            store.push_event(
                "fill_received",
                {
                    "asset": entry["asset"],
                    "entry_price": round(entry_price, 4),
                    "shares": round(shares, 4),
                    "source": fill["source"],
                },
            )

            slippage_pct = ((entry_price - entry["quoted_price"]) / entry["quoted_price"]) if entry["quoted_price"] > 0 else 0.0
            if slippage_pct >= cfg["FILL_SLIPPAGE_WARN_PCT"]:
                store.push_event(
                    "slippage_warning",
                    {
                        "quoted": round(entry["quoted_price"], 4),
                        "fill": round(entry_price, 4),
                        "pct": round(slippage_pct * 100, 2),
                    },
                    level="warn",
                )

            if shares > 0:
                limit_sell = self._fn["place_limit_sell"](
                    client,
                    entry["token_id"],
                    shares,
                    0.99,
                    dry_run=dry_run,
                )
                if limit_sell is not None:
                    store.push_event(
                        "take_profit_order_placed",
                        {
                            "asset": entry["asset"],
                            "price": round(float(limit_sell.get("_limit_price", 0.99)), 4),
                            "shares": round(float(limit_sell.get("_shares", shares)), 4),
                            "order_type": limit_sell.get("_order_type"),
                        },
                    )
                else:
                    store.push_event(
                        "take_profit_order_failed",
                        {
                            "asset": entry["asset"],
                            "price": 0.99,
                            "shares": round(shares, 4),
                        },
                        level="warn",
                    )

        starting_balance = float(store.get_stats()["current_balance"])
        self._wait_for_resolution(markets, auto_redeemer, cfg)
        self._request_redeem(reason="post_resolution")

        new_balance = get_balance(client)
        pnl = new_balance - starting_balance
        store.update_stats({"current_balance": new_balance})

        redeemed = pnl > -(entry["bet_amount"] * 0.98)
        outcome = self._classify_outcome(pnl, {"triggered": False}, dry_run, redeemed)
        stats = store.get_stats()
        if outcome != "unsettled":
            stats["total_pnl"] += pnl
        store.update_stats(stats)
        store.push_event(
            "round_result",
            {
                "round": round_num,
                "outcome": outcome,
                "pnl": round(pnl, 4) if outcome != "unsettled" else None,
                "balance": round(new_balance, 2),
                "stop_triggered": False,
            },
        )
        store.set_current_round(None)
        store.set_latest_evaluation(None)

    def _attempt_fair_value_entry(self, client, markets: dict[str, Any], auto_redeemer, cfg: dict[str, Any], dry_run: bool) -> dict[str, Any] | None:
        get_balance = self._fn["get_balance"]
        get_token_prices_batch = self._fn["get_token_prices_batch"]
        place_bet = self._fn["place_bet"]

        balance_snapshot = None
        balance_snapshot_ts = 0.0
        vol_cache: dict[str, dict[str, Any]] = {asset: {"snapshot": None, "ts": 0.0} for asset in markets}
        priority_map = {asset: idx for idx, asset in enumerate(self._ordered_assets(cfg["ENABLED_ASSETS"], cfg["ASSET_PRIORITY"]))}

        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            live_markets = {
                asset: market
                for asset, market in markets.items()
                if max(0.0, (market["end_date"] - now).total_seconds()) > 0
            }
            if not live_markets:
                return None

            candidates: list[dict[str, Any]] = []
            asset_evaluations: dict[str, Any] = {}

            for asset in self._ordered_assets(list(live_markets.keys()), cfg["ASSET_PRIORITY"]):
                market = live_markets[asset]
                seconds_left = max(0.0, (market["end_date"] - now).total_seconds())
                params = get_dynamic_entry_params(seconds_left, cfg["ENTRY_PROFILE_POINTS"])
                min_edge = float(params["min_odds"])
                capital_pct = float(params["capital_pct"])

                vol_snapshot = self._get_vol_snapshot(asset, cfg, vol_cache.setdefault(asset, {"snapshot": None, "ts": 0.0}))
                min_edge, capital_pct, vol_regime = self._apply_volatility_profile(cfg, min_edge, capital_pct, vol_snapshot)

                reference = fetch_reference_snapshot(asset)
                if not reference:
                    asset_evaluations[asset] = {
                        "asset": asset,
                        "decision": "watching",
                        "reason": f"Waiting for {asset.upper()} reference snapshot",
                        "side": None,
                        "seconds_left": round(seconds_left, 1),
                        "min_edge": round(min_edge, 4),
                    }
                    continue

                if (
                    abs(reference["distance_pct"]) < cfg["FAIR_VALUE_NO_TRADE_BAND_PCT"]
                    and seconds_left > cfg["ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS"]
                ):
                    asset_evaluations[asset] = {
                        "asset": asset,
                        "decision": "watching",
                        "reason": "Move still inside no-trade band",
                        "side": None,
                        "seconds_left": round(seconds_left, 1),
                        "open_price": round(reference["open_price"], 2),
                        "current_price": round(reference["current_price"], 2),
                        "distance_pct": round(reference["distance_pct"] * 100, 4),
                        "vol_regime": vol_regime,
                        "min_edge": round(min_edge, 4),
                    }
                    continue

                asks = get_token_prices_batch(client, [market["up_token"], market["down_token"]], side="BUY")
                bids = get_token_prices_batch(client, [market["up_token"], market["down_token"]], side="SELL")
                fair_up, model_regime, rem_vol_pct = estimate_up_probability(
                    current_price=reference["current_price"],
                    open_price=reference["open_price"],
                    seconds_left=seconds_left,
                    sigma_floor_pct=cfg["FAIR_VALUE_SIGMA_FLOOR_PCT"],
                    volatility_snapshot=vol_snapshot,
                    regime_multipliers={"low": 0.9, "mid": 1.0, "high": 1.2, "extreme": 1.45},
                    thresholds={
                        "low": cfg["VOLATILITY_LOW_THRESHOLD"],
                        "high": cfg["VOLATILITY_HIGH_THRESHOLD"],
                        "extreme": cfg["VOLATILITY_EXTREME_THRESHOLD"],
                    },
                )
                evaluation = evaluate_trade_setup(
                    market=market,
                    prices=asks,
                    bids=bids,
                    fair_up=fair_up,
                    min_edge=min_edge,
                    max_odds=cfg["MAX_ODDS"],
                    max_spread=cfg["FAIR_VALUE_MAX_SPREAD"],
                    min_model_probability=cfg["FAIR_VALUE_MIN_MODEL_PROBABILITY"],
                    min_market_probability=cfg["FAIR_VALUE_MIN_MARKET_PROBABILITY"],
                    ignore_edge_filter=bool(cfg.get("IGNORE_EDGE_FILTER", False)),
                    certainty_seconds_threshold=cfg["CERTAINTY_SECONDS_THRESHOLD"],
                    certainty_avg_threshold=cfg["CERTAINTY_AVG_THRESHOLD"],
                    seconds_left=seconds_left,
                )
                candidate = evaluation["candidate"]
                if candidate:
                    candidate["priority"] = priority_map.get(asset, 999)
                    candidate["seconds_left"] = seconds_left
                    candidate["capital_pct"] = capital_pct
                    candidate["vol_regime"] = vol_regime
                    candidate["model_regime"] = model_regime
                    candidate["remaining_vol_pct"] = rem_vol_pct
                    candidate["reference"] = reference
                    candidates.append(candidate)

                up_eval = evaluation["sides"].get("up", {})
                down_eval = evaluation["sides"].get("down", {})

                up_model = float(up_eval.get("fair_value") or 0)
                down_model = float(down_eval.get("fair_value") or 0)
                up_market = float(up_eval.get("buy_price") or 0)
                down_market = float(down_eval.get("buy_price") or 0)
                store.record_eval_snapshot(asset, up_model, down_model, up_market, down_market)
                rolling = store.get_rolling_stats(asset, cfg["ROLLING_WINDOW_SECONDS"])

                asset_evaluations[asset] = {
                    "asset": asset,
                    "decision": evaluation["decision"],
                    "reason": evaluation["reason"],
                    "forced_side": evaluation.get("forced_side"),
                    "side": candidate["side"] if candidate else evaluation["best_side"]["side"] if evaluation["best_side"] else None,
                    "seconds_left": round(seconds_left, 1),
                    "open_price": round(reference["open_price"], 2),
                    "current_price": round(reference["current_price"], 2),
                    "distance_pct": round(reference["distance_pct"] * 100, 4),
                    "vol_regime": vol_regime,
                    "model_regime": model_regime,
                    "remaining_vol_pct": round(rem_vol_pct * 100, 4),
                    "min_edge": round(min_edge, 4),
                    "rolling": rolling,
                    "up": {
                        "fair_value": round(up_eval.get("fair_value"), 4) if up_eval.get("fair_value") is not None else None,
                        "buy_price": round(up_eval.get("buy_price"), 4) if up_eval.get("buy_price") is not None else None,
                        "edge": round(up_eval.get("edge"), 4) if up_eval.get("edge") is not None else None,
                        "spread": round(up_eval.get("spread"), 4) if up_eval.get("spread") is not None else None,
                        "avg_prob": round(up_eval.get("avg_prob"), 4) if up_eval.get("avg_prob") is not None else None,
                        "eligible": bool(up_eval.get("eligible")),
                        "reason": up_eval.get("reason"),
                        "checks": up_eval.get("checks", {}),
                    },
                    "down": {
                        "fair_value": round(down_eval.get("fair_value"), 4) if down_eval.get("fair_value") is not None else None,
                        "buy_price": round(down_eval.get("buy_price"), 4) if down_eval.get("buy_price") is not None else None,
                        "edge": round(down_eval.get("edge"), 4) if down_eval.get("edge") is not None else None,
                        "spread": round(down_eval.get("spread"), 4) if down_eval.get("spread") is not None else None,
                        "avg_prob": round(down_eval.get("avg_prob"), 4) if down_eval.get("avg_prob") is not None else None,
                        "eligible": bool(down_eval.get("eligible")),
                        "reason": down_eval.get("reason"),
                        "checks": down_eval.get("checks", {}),
                    },
                }

            selected_candidate = None
            if candidates:
                if bool(cfg.get("IGNORE_EDGE_FILTER", False)):
                    candidates.sort(key=lambda item: (item["priority"], -item.get("avg_prob", 0.0), -item["buy_price"]))
                else:
                    candidates.sort(key=lambda item: (item["priority"], -item["edge"], -item["buy_price"]))
                selected_candidate = candidates[0]

            if selected_candidate is not None:
                if selected_candidate["seconds_left"] > float(cfg["ENTRY_START_SECONDS"]):
                    asset = str(selected_candidate["asset"])
                    asset_eval = asset_evaluations.get(asset)
                    if asset_eval is not None:
                        asset_eval["decision"] = "watching"
                        asset_eval["reason"] = (
                            f"Live monitoring only. Entries start at {int(cfg['ENTRY_START_SECONDS'])}s"
                        )
                    selected_candidate = None

            store.set_latest_evaluation(
                self._build_live_evaluation(
                    cfg=cfg,
                    asset_evaluations=asset_evaluations,
                    selected_candidate=selected_candidate,
                )
            )

            if selected_candidate is None:
                self._request_redeem(reason="entry_loop")
                self._interruptible_sleep(
                    cfg["ENTRY_CHECK_INTERVAL_FAST_SECONDS"]
                    if self._seconds_left_for_markets(live_markets) <= cfg["ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS"]
                    else cfg["ENTRY_CHECK_INTERVAL_SECONDS"]
                )
                continue

            now_ts = time.time()
            if balance_snapshot is None or (now_ts - balance_snapshot_ts) >= cfg["ENTRY_BALANCE_REFRESH_SECONDS"]:
                balance_snapshot = get_balance(client)
                balance_snapshot_ts = now_ts
            target_amount = self._target_bet_amount(balance_snapshot, selected_candidate["capital_pct"], cfg)
            bet_amount = min(balance_snapshot, math.floor(target_amount * 100) / 100)
            if bet_amount < cfg["MIN_BET_USDC"]:
                return None

            seconds_left = selected_candidate["seconds_left"]
            use_market = (
                selected_candidate["edge"] >= cfg["FAIR_VALUE_AGGRESSIVE_EDGE"]
                or seconds_left <= cfg["ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS"]
            )
            limit_cap = min(
                cfg["MAX_ODDS"],
                round(selected_candidate["fair_value"] - cfg["FAIR_VALUE_REQUOTE_THRESHOLD"], 4),
            )
            execution_mode = "market"
            if (
                cfg["ENTRY_LIMIT_FLOATING_ENABLED"]
                and not use_market
                and limit_cap > selected_candidate["buy_price"]
            ):
                execution_mode = "limit"

            store.push_event(
                "bot2_signal",
                {
                    "asset": selected_candidate["asset"],
                    "side": selected_candidate["side"],
                    "fair_value": round(selected_candidate["fair_value"], 4),
                    "buy_price": round(selected_candidate["buy_price"], 4),
                    "edge": round(selected_candidate["edge"], 4),
                    "spread": round(selected_candidate["spread"], 4),
                    "seconds_left": round(seconds_left, 1),
                    "open_price": round(selected_candidate["reference"]["open_price"], 2),
                    "current_price": round(selected_candidate["reference"]["current_price"], 2),
                    "distance_pct": round(selected_candidate["reference"]["distance_pct"] * 100, 4),
                    "vol_regime": selected_candidate["vol_regime"],
                    "model_regime": selected_candidate["model_regime"],
                    "remaining_vol_pct": round(selected_candidate["remaining_vol_pct"] * 100, 4),
                },
                level="debug",
            )

            order_response = place_bet(
                client,
                selected_candidate["token_id"],
                bet_amount,
                dry_run=dry_run,
                execution_mode=execution_mode,
                limit_max_price=limit_cap,
                limit_reprice_interval_seconds=cfg["ENTRY_LIMIT_REPRICE_INTERVAL_SECONDS"],
                limit_max_reprices=cfg["ENTRY_LIMIT_MAX_REPRICES"],
            )
            if order_response is None:
                self._interruptible_sleep(cfg["ENTRY_CHECK_INTERVAL_FAST_SECONDS"])
                continue

            effective_amount = bet_amount
            if isinstance(order_response, dict):
                effective_amount = float(order_response.get("_effective_amount", bet_amount))

            return {
                "asset": selected_candidate["asset"],
                "market": selected_candidate["market"],
                "token_id": selected_candidate["token_id"],
                "side": selected_candidate["side"],
                "quoted_price": selected_candidate["buy_price"],
                "bet_amount": effective_amount,
                "seconds_left_at_entry": seconds_left,
                "order_response": order_response,
            }

        return None

    @staticmethod
    def _ordered_assets(assets: list[str], priority: list[str]) -> list[str]:
        asset_set = {str(asset).lower() for asset in assets}
        return [asset for asset in priority if asset in asset_set]

    def _discover_markets(self, assets: list[str]) -> dict[str, Any]:
        markets: dict[str, Any] = {}
        for asset in assets:
            market = self._fn["find_market"](asset)
            if market is not None:
                market["asset"] = asset
                markets[asset] = market
        return markets

    @staticmethod
    def _seconds_left_for_markets(markets: dict[str, Any]) -> float:
        now = datetime.now(timezone.utc)
        values = [max(0.0, (market["end_date"] - now).total_seconds()) for market in markets.values()]
        return min(values) if values else 0.0

    @staticmethod
    def _stats_key_for_asset(asset: str) -> str:
        return {
            "btc": "total_btc_entries",
            "eth": "total_eth_entries",
            "sol": "total_sol_entries",
        }.get(str(asset).lower(), "total_entries")

    @staticmethod
    def _target_bet_amount(balance_snapshot: float, capital_pct: float, cfg: dict[str, Any]) -> float:
        if str(cfg.get("BET_SIZING_MODE", "dynamic")).lower() == "fixed":
            return max(1.0, float(cfg["MIN_BET_USDC"]))
        return max(float(cfg["MIN_BET_USDC"]), float(balance_snapshot) * float(capital_pct))

    def _build_live_evaluation(
        self,
        *,
        cfg: dict[str, Any],
        asset_evaluations: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> dict[str, Any]:
        focus_asset = selected_candidate["asset"] if selected_candidate else None
        if focus_asset is None:
            ordered_assets = self._ordered_assets(list(asset_evaluations.keys()), cfg["ASSET_PRIORITY"])
            if ordered_assets:
                focus_asset = ordered_assets[0]
        focus_eval = asset_evaluations.get(focus_asset or "", {})
        return {
            "decision": "eligible" if selected_candidate else "watching",
            "reason": (
                f"{str(selected_candidate['asset']).upper()} {str(selected_candidate['side']).upper()} passes all entry filters"
                if selected_candidate
                else focus_eval.get("reason", "Watching for a better setup")
            ),
            "asset": focus_asset,
            "side": focus_eval.get("side"),
            "min_model_probability": cfg["FAIR_VALUE_MIN_MODEL_PROBABILITY"],
            "min_market_probability": cfg["FAIR_VALUE_MIN_MARKET_PROBABILITY"],
            "ignore_edge_filter": bool(cfg.get("IGNORE_EDGE_FILTER", False)),
            "live_monitor_start_seconds": cfg.get("LIVE_MONITOR_START_SECONDS"),
            "entry_start_seconds": cfg["ENTRY_START_SECONDS"],
            "certainty_avg_threshold": cfg["CERTAINTY_AVG_THRESHOLD"],
            "certainty_seconds_threshold": cfg["CERTAINTY_SECONDS_THRESHOLD"],
            "rolling_window_seconds": cfg["ROLLING_WINDOW_SECONDS"],
            "bet_sizing_mode": cfg["BET_SIZING_MODE"],
            "fixed_bet_usdc": max(1.0, float(cfg["MIN_BET_USDC"])),
            "assets": asset_evaluations,
        }

    def _wait_until_entry_window(self, markets: dict[str, Any], auto_redeemer, cfg: dict[str, Any]) -> None:
        seconds_left = self._seconds_left_for_markets(markets)
        evaluation_start_seconds = float(cfg.get("LIVE_MONITOR_START_SECONDS", cfg["ENTRY_START_SECONDS"]))
        if seconds_left <= evaluation_start_seconds:
            return
        wait_remaining = seconds_left - evaluation_start_seconds
        while wait_remaining > 0 and not self._stop_event.is_set():
            chunk = min(cfg["WAIT_LOG_INTERVAL_SECONDS"], wait_remaining)
            self._interruptible_sleep(chunk)
            wait_remaining -= chunk
            self._request_redeem(reason="pre_entry_wait")

    def _wait_for_resolution(self, markets: dict[str, Any], auto_redeemer, cfg: dict[str, Any]) -> None:
        seconds_left = self._seconds_left_for_markets(markets)
        wait = seconds_left + cfg["POST_RESOLUTION_BUFFER_SECONDS"]
        while wait > 0 and not self._stop_event.is_set():
            chunk = min(cfg["WAIT_LOG_INTERVAL_SECONDS"], wait)
            self._interruptible_sleep(chunk)
            wait -= chunk
            self._request_redeem(reason="wait_resolution")

    def _interruptible_sleep(self, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end and not self._stop_event.is_set():
            time.sleep(min(0.25, end - time.time()))

    def _get_vol_snapshot(self, asset: str, cfg: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any] | None:
        now_ts = time.time()
        refresh_seconds = max(1.0, float(cfg["VOLATILITY_REFRESH_SECONDS"]))
        if cache.get("snapshot") is not None and (now_ts - cache.get("ts", 0.0)) < refresh_seconds:
            return cache["snapshot"]
        candles = fetch_candles(
            asset,
            interval=str(cfg["VOLATILITY_INTERVAL"]),
            limit=max(3, int(cfg["VOLATILITY_LOOKBACK_CANDLES"])),
        )
        snapshot = build_snapshot_from_candles(candles)
        cache["snapshot"] = snapshot
        cache["ts"] = now_ts
        return snapshot

    @staticmethod
    def _apply_volatility_profile(cfg: dict[str, Any], min_edge: float, capital_pct: float, snapshot: dict[str, Any] | None) -> tuple[float, float, str]:
        if not cfg["VOLATILITY_FILTER_ENABLED"] or not snapshot:
            return min_edge, capital_pct, "off"
        score = float(snapshot.get("score", 0.0))
        if score >= cfg["VOLATILITY_EXTREME_THRESHOLD"]:
            return (
                min(0.5, min_edge + cfg["VOLATILITY_MIN_ODDS_BUMP_EXTREME"]),
                capital_pct * cfg["VOLATILITY_CAPITAL_MULT_EXTREME"],
                "extreme",
            )
        if score >= cfg["VOLATILITY_HIGH_THRESHOLD"]:
            return (
                min(0.5, min_edge + cfg["VOLATILITY_MIN_ODDS_BUMP_HIGH"]),
                capital_pct * cfg["VOLATILITY_CAPITAL_MULT_HIGH"],
                "high",
            )
        if score <= cfg["VOLATILITY_LOW_THRESHOLD"]:
            return (
                max(0.005, min_edge - 0.003),
                min(1.0, capital_pct * cfg["VOLATILITY_CAPITAL_MULT_LOW"]),
                "low",
            )
        return min_edge, capital_pct, "mid"

    def _try_redeem(self, auto_redeemer, cfg: dict[str, Any], *, force: bool = False, reason: str = "loop") -> dict[str, Any]:
        if not cfg.get("AUTO_REDEEM_ENABLED", True):
            return {"attempted": False, "claimed": 0, "pending": 0, "errors": []}

        now = time.time()
        attempt_interval = max(1, int(cfg["AUTO_REDEEM_ATTEMPT_INTERVAL_SECONDS"]))
        probe_interval = max(1, int(cfg["AUTO_REDEEM_PROBE_INTERVAL_SECONDS"]))
        rate_limit_buffer = max(0, int(cfg["AUTO_REDEEM_RATE_LIMIT_BUFFER_SECONDS"]))

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
            store.push_event("redeem_pending_detected", {"count": len(pending_conditions), "reason": reason})

        if (not force) and now < self._next_redeem_attempt_ts:
            return {"attempted": False, "claimed": 0, "pending": 0, "errors": []}

        self._redeem_attempt_counter += 1
        max_conditions = 10 if force else cfg["AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE"]
        store.push_event(
            "redeem_attempt",
            {
                "attempt": self._redeem_attempt_counter,
                "reason": reason,
                "max_conditions": max_conditions,
                "force": force,
            },
        )
        redeem = auto_redeemer.redeem_once(max_conditions=max_conditions)
        claimed = int(redeem.get("claimed", 0) or 0)
        pending = int(redeem.get("pending", 0) or 0)
        errors = redeem.get("errors") or []

        if claimed > 0:
            store.push_event(
                "redeem_claimed",
                {"claimed": claimed, "pending": pending, "attempt": self._redeem_attempt_counter},
            )
        if errors:
            store.push_event(
                "redeem_error",
                {"errors": errors[:3], "attempt": self._redeem_attempt_counter},
                level="warn",
            )

        reset_seconds = self._extract_rate_limit_reset_seconds(errors)
        if reset_seconds is not None:
            wait_seconds = max(1, int(reset_seconds) + rate_limit_buffer)
            self._redeem_pending = True
            self._next_redeem_attempt_ts = now + wait_seconds
            store.push_event(
                "redeem_rate_limited",
                {"retry_in_seconds": wait_seconds, "attempt": self._redeem_attempt_counter},
                level="warn",
            )
            return redeem

        if errors or claimed > 0 or pending > 0:
            self._redeem_pending = True
            self._next_redeem_attempt_ts = now + attempt_interval
            return redeem

        self._redeem_pending = False
        self._next_redeem_probe_ts = now + probe_interval
        self._next_redeem_attempt_ts = 0.0
        return redeem

    @staticmethod
    def _extract_rate_limit_reset_seconds(errors: list[str]) -> int | None:
        if not errors:
            return None
        best = None
        for error in errors:
            match = re.search(r"resets?\s+in\s+(\d+)\s+seconds?", str(error).lower())
            if match:
                seconds = int(match.group(1))
                best = seconds if best is None else max(best, seconds)
        return best

    @staticmethod
    def _classify_outcome(pnl: float, stop_info: dict[str, Any], dry_run: bool, redeemed: bool = True) -> str:
        if dry_run:
            return "dry_run"
        if not redeemed:
            return "unsettled"
        if stop_info.get("triggered"):
            return "loss" if pnl < 0 else "win"
        if pnl > 0:
            return "win"
        if pnl < 0:
            return "loss"
        return "even"


bot2_manager = Bot2Manager()
