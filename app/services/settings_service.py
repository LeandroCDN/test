import json
import os
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app import config as cfg


SETTINGS_FILE = os.getenv(
    "BOT_SETTINGS_FILE",
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "runtime_settings.json"),
)
SETTINGS_FILE = os.path.normpath(SETTINGS_FILE)

SUPPORTED_ASSETS = {"btc", "eth"}


class AppSettings(BaseModel):
    enabled_assets: list[str] = Field(default_factory=lambda: ["btc", "eth"])

    entry_start_seconds: int = cfg.ENTRY_START_SECONDS
    entry_check_interval_seconds: float = cfg.ENTRY_CHECK_INTERVAL_SECONDS
    entry_check_interval_fast_seconds: float = cfg.ENTRY_CHECK_INTERVAL_FAST_SECONDS
    entry_check_interval_fast_threshold_seconds: int = cfg.ENTRY_CHECK_INTERVAL_FAST_THRESHOLD_SECONDS
    entry_balance_refresh_seconds: int = cfg.ENTRY_BALANCE_REFRESH_SECONDS
    entry_profile_points: list[list[float]] = Field(
        default_factory=lambda: [[p[0], p[1], p[2]] for p in cfg.ENTRY_PROFILE_POINTS]
    )

    min_bet_usdc: float = cfg.MIN_BET_USDC
    max_odds: float = cfg.MAX_ODDS
    fill_slippage_warn_pct: float = cfg.FILL_SLIPPAGE_WARN_PCT
    entry_lock_market_on_high_odds_reject: bool = cfg.ENTRY_LOCK_MARKET_ON_HIGH_ODDS_REJECT

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

    @field_validator("enabled_assets")
    @classmethod
    def _validate_assets(cls, values: list[str]) -> list[str]:
        cleaned = []
        for v in values:
            s = str(v).strip().lower()
            if s:
                cleaned.append(s)
        cleaned = sorted(set(cleaned))
        if not cleaned:
            raise ValueError("At least one asset must be enabled")
        unsupported = [a for a in cleaned if a not in SUPPORTED_ASSETS]
        if unsupported:
            raise ValueError(f"Unsupported assets: {', '.join(unsupported)}")
        return cleaned

    @field_validator("entry_profile_points")
    @classmethod
    def _validate_profile_points(cls, points: list[list[float]]) -> list[list[float]]:
        if not points:
            raise ValueError("entry_profile_points cannot be empty")
        norm: list[list[float]] = []
        for p in points:
            if len(p) != 3:
                raise ValueError("Each profile point must be [seconds_left, min_odds, capital_pct]")
            sec, odds, cap = float(p[0]), float(p[1]), float(p[2])
            if sec < 0:
                raise ValueError("seconds_left must be >= 0")
            if not (0 < odds < 1):
                raise ValueError("min_odds must be between 0 and 1")
            if not (0 < cap <= 1):
                raise ValueError("capital_pct must be between 0 and 1")
            norm.append([sec, odds, cap])
        norm.sort(key=lambda x: x[0], reverse=True)
        return norm


def _defaults() -> dict[str, Any]:
    return AppSettings().model_dump()


def _read_raw_settings() -> dict[str, Any]:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_settings() -> dict[str, Any]:
    merged = _defaults()
    merged.update(_read_raw_settings())
    validated = AppSettings.model_validate(merged)
    return validated.model_dump()


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    validated = AppSettings.model_validate(settings).model_dump()
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(validated, f, indent=2, ensure_ascii=True)
    return validated


def settings_equal(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return AppSettings.model_validate(a).model_dump() == AppSettings.model_validate(b).model_dump()
