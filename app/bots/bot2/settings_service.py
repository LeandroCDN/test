from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.bots.bot2 import config as cfg

SETTINGS_FILE = os.getenv(
    "BOT2_SETTINGS_FILE",
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir, "runtime_settings_bot2.json"),
)
SETTINGS_FILE = os.path.normpath(SETTINGS_FILE)


class Bot2Settings(BaseModel):
    enabled_assets: list[str] = Field(default_factory=lambda: list(cfg.ENABLED_ASSETS))

    entry_start_seconds: int = cfg.ENTRY_START_SECONDS
    live_monitor_start_seconds: int = cfg.LIVE_MONITOR_START_SECONDS
    entry_check_interval_seconds: float = cfg.ENTRY_CHECK_INTERVAL_SECONDS
    entry_check_interval_fast_seconds: float = cfg.ENTRY_CHECK_INTERVAL_FAST_SECONDS
    entry_check_interval_fast_threshold_seconds: int = cfg.ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS
    entry_limit_floating_enabled: bool = cfg.ENTRY_LIMIT_FLOATING_ENABLED
    entry_limit_phase_ratio: float = cfg.ENTRY_LIMIT_PHASE_RATIO
    entry_limit_reprice_interval_seconds: float = cfg.ENTRY_LIMIT_REPRICE_INTERVAL_SECONDS
    entry_limit_max_reprices: int = cfg.ENTRY_LIMIT_MAX_REPRICES
    entry_balance_refresh_seconds: int = cfg.ENTRY_BALANCE_REFRESH_SECONDS
    entry_profile_points: list[list[float]] = Field(
        default_factory=lambda: [[p[0], p[1], p[2]] for p in cfg.ENTRY_PROFILE_POINTS]
    )

    min_bet_usdc: float = cfg.MIN_BET_USDC
    bet_sizing_mode: str = cfg.BET_SIZING_MODE
    max_odds: float = cfg.MAX_ODDS
    fill_slippage_warn_pct: float = cfg.FILL_SLIPPAGE_WARN_PCT

    poll_interval_seconds: int = cfg.POLL_INTERVAL_SECONDS
    post_resolution_buffer_seconds: int = cfg.POST_RESOLUTION_BUFFER_SECONDS
    wait_log_interval_seconds: int = cfg.WAIT_LOG_INTERVAL_SECONDS

    auto_redeem_enabled: bool = cfg.AUTO_REDEEM_ENABLED
    auto_redeem_max_conditions_per_cycle: int = cfg.AUTO_REDEEM_MAX_CONDITIONS_PER_CYCLE
    auto_redeem_attempt_interval_seconds: int = cfg.AUTO_REDEEM_ATTEMPT_INTERVAL_SECONDS
    auto_redeem_probe_interval_seconds: int = cfg.AUTO_REDEEM_PROBE_INTERVAL_SECONDS
    auto_redeem_rate_limit_buffer_seconds: int = cfg.AUTO_REDEEM_RATE_LIMIT_BUFFER_SECONDS

    stop_loss_enabled: bool = cfg.STOP_LOSS_ENABLED
    stop_loss_pct: float = cfg.STOP_LOSS_PCT
    stop_loss_poll_seconds: int = cfg.STOP_LOSS_POLL_SECONDS
    stop_loss_confirm_ticks: int = cfg.STOP_LOSS_CONFIRM_TICKS
    stop_loss_retry_seconds: int = cfg.STOP_LOSS_RETRY_SECONDS

    volatility_filter_enabled: bool = cfg.VOLATILITY_FILTER_ENABLED
    volatility_refresh_seconds: int = cfg.VOLATILITY_REFRESH_SECONDS
    volatility_interval: str = cfg.VOLATILITY_INTERVAL
    volatility_lookback_candles: int = cfg.VOLATILITY_LOOKBACK_CANDLES
    volatility_low_threshold: float = cfg.VOLATILITY_LOW_THRESHOLD
    volatility_high_threshold: float = cfg.VOLATILITY_HIGH_THRESHOLD
    volatility_extreme_threshold: float = cfg.VOLATILITY_EXTREME_THRESHOLD
    volatility_min_odds_bump_high: float = cfg.VOLATILITY_MIN_ODDS_BUMP_HIGH
    volatility_min_odds_bump_extreme: float = cfg.VOLATILITY_MIN_ODDS_BUMP_EXTREME
    volatility_capital_mult_low: float = cfg.VOLATILITY_CAPITAL_MULT_LOW
    volatility_capital_mult_high: float = cfg.VOLATILITY_CAPITAL_MULT_HIGH
    volatility_capital_mult_extreme: float = cfg.VOLATILITY_CAPITAL_MULT_EXTREME

    fair_value_sigma_floor_pct: float = cfg.FAIR_VALUE_SIGMA_FLOOR_PCT
    fair_value_no_trade_band_pct: float = cfg.FAIR_VALUE_NO_TRADE_BAND_PCT
    fair_value_max_spread: float = cfg.FAIR_VALUE_MAX_SPREAD
    fair_value_requote_threshold: float = cfg.FAIR_VALUE_REQUOTE_THRESHOLD
    fair_value_aggressive_edge: float = cfg.FAIR_VALUE_AGGRESSIVE_EDGE
    fair_value_min_model_probability: float = cfg.FAIR_VALUE_MIN_MODEL_PROBABILITY
    fair_value_min_market_probability: float = cfg.FAIR_VALUE_MIN_MARKET_PROBABILITY
    ignore_edge_filter: bool = cfg.IGNORE_EDGE_FILTER
    certainty_seconds_threshold: int = cfg.CERTAINTY_SECONDS_THRESHOLD
    certainty_avg_threshold: float = cfg.CERTAINTY_AVG_THRESHOLD
    rolling_window_seconds: int = cfg.ROLLING_WINDOW_SECONDS

    @field_validator("enabled_assets")
    @classmethod
    def _validate_assets(cls, values: list[str]) -> list[str]:
        cleaned = sorted({str(v).strip().lower() for v in values if str(v).strip()})
        allowed = {"btc", "eth", "sol"}
        invalid = [asset for asset in cleaned if asset not in allowed]
        if invalid:
            raise ValueError(f"Unsupported bot 2 assets: {', '.join(invalid)}")
        if not cleaned:
            raise ValueError("enabled_assets cannot be empty")
        return cleaned

    @field_validator("bet_sizing_mode")
    @classmethod
    def _validate_bet_sizing_mode(cls, value: str) -> str:
        mode = str(value).strip().lower()
        if mode not in {"dynamic", "fixed"}:
            raise ValueError("bet_sizing_mode must be 'dynamic' or 'fixed'")
        return mode

    @field_validator("entry_profile_points")
    @classmethod
    def _validate_profile_points(cls, points: list[list[float]]) -> list[list[float]]:
        if not points:
            raise ValueError("entry_profile_points cannot be empty")
        normalized: list[list[float]] = []
        for point in points:
            if len(point) != 3:
                raise ValueError("Each profile point must be [seconds_left, min_edge, capital_pct]")
            sec, edge, capital = float(point[0]), float(point[1]), float(point[2])
            if sec < 0:
                raise ValueError("seconds_left must be >= 0")
            if edge <= 0 or edge >= 1:
                raise ValueError("min_edge must be between 0 and 1")
            if capital <= 0 or capital > 1:
                raise ValueError("capital_pct must be between 0 and 1")
            normalized.append([sec, edge, capital])
        normalized.sort(key=lambda item: item[0], reverse=True)
        return normalized

    @field_validator("entry_limit_phase_ratio")
    @classmethod
    def _validate_phase_ratio(cls, value: float) -> float:
        value = float(value)
        if not (0 < value < 1):
            raise ValueError("entry_limit_phase_ratio must be between 0 and 1")
        return value

    @field_validator("entry_start_seconds", "live_monitor_start_seconds", "certainty_seconds_threshold", "rolling_window_seconds")
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        value = int(value)
        if value <= 0:
            raise ValueError("Value must be > 0")
        return value

    @field_validator(
        "volatility_low_threshold",
        "volatility_high_threshold",
        "volatility_extreme_threshold",
        "volatility_min_odds_bump_high",
        "volatility_min_odds_bump_extreme",
        "fair_value_sigma_floor_pct",
        "fair_value_no_trade_band_pct",
        "fair_value_max_spread",
        "fair_value_requote_threshold",
        "fair_value_aggressive_edge",
        "certainty_avg_threshold",
    )
    @classmethod
    def _validate_non_negative(cls, value: float) -> float:
        value = float(value)
        if value < 0:
            raise ValueError("Value must be >= 0")
        return value

    @field_validator("fair_value_min_model_probability", "fair_value_min_market_probability")
    @classmethod
    def _validate_probability_threshold(cls, value: float) -> float:
        value = float(value)
        if value < 0:
            raise ValueError("Probability threshold must be >= 0")
        if value > 1:
            if value <= 100:
                value = value / 100.0
            else:
                raise ValueError("Probability threshold must be between 0 and 1, or 0 and 100")
        if value > 1:
            raise ValueError("Probability threshold must be between 0 and 1")
        return value

    @field_validator(
        "volatility_capital_mult_low",
        "volatility_capital_mult_high",
        "volatility_capital_mult_extreme",
    )
    @classmethod
    def _validate_positive_multiplier(cls, value: float) -> float:
        value = float(value)
        if value <= 0:
            raise ValueError("Multiplier must be > 0")
        return value


def _defaults() -> dict[str, Any]:
    return Bot2Settings().model_dump()


def _read_raw_settings() -> dict[str, Any]:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def load_settings() -> dict[str, Any]:
    merged = _defaults()
    raw = _read_raw_settings()
    legacy_min_probability = raw.get("fair_value_min_probability")
    if legacy_min_probability is not None:
        raw.setdefault("fair_value_min_model_probability", legacy_min_probability)
        raw.setdefault("fair_value_min_market_probability", legacy_min_probability)
    legacy_cert = raw.pop("certainty_probability_threshold", None)
    if legacy_cert is not None:
        raw.setdefault("certainty_avg_threshold", legacy_cert)
    merged.update(raw)
    validated = Bot2Settings.model_validate(merged)
    return validated.model_dump()


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    validated = Bot2Settings.model_validate(settings).model_dump()
    with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
        json.dump(validated, fh, indent=2, ensure_ascii=True)
    return validated


def settings_equal(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return Bot2Settings.model_validate(a).model_dump() == Bot2Settings.model_validate(b).model_dump()
