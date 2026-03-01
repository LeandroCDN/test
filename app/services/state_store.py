"""
In-memory state and event store shared between the bot worker and the API layer.

Thread-safe: all mutations go through a lock so the FastAPI server
can read snapshots without races against the bot thread.
"""

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any


_lock = threading.Lock()

_MAX_EVENTS = 500

_worker_status: str = "stopped"  # stopped | starting | running | stopping | paused
_entry_paused: bool = False

_stats: dict[str, Any] = {
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
    "start_balance": 0.0,
    "current_balance": 0.0,
}

_current_round: dict[str, Any] | None = None

_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)

_started_at: float | None = None


# ── helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_id() -> str:
    return f"{time.time_ns()}"


# ── worker status ────────────────────────────────────────────────

def get_worker_status() -> str:
    with _lock:
        return _worker_status


def set_worker_status(status: str) -> None:
    global _worker_status, _started_at
    with _lock:
        _worker_status = status
        if status == "running" and _started_at is None:
            _started_at = time.time()
        if status == "stopped":
            _started_at = None


# ── entry pause ──────────────────────────────────────────────────

def is_entry_paused() -> bool:
    with _lock:
        return _entry_paused


def set_entry_paused(paused: bool) -> None:
    global _entry_paused
    with _lock:
        _entry_paused = paused


# ── stats ────────────────────────────────────────────────────────

def update_stats(patch: dict[str, Any]) -> None:
    with _lock:
        _stats.update(patch)


def get_stats() -> dict[str, Any]:
    with _lock:
        return dict(_stats)


# ── current round ────────────────────────────────────────────────

def set_current_round(info: dict[str, Any] | None) -> None:
    global _current_round
    with _lock:
        _current_round = info


def get_current_round() -> dict[str, Any] | None:
    with _lock:
        return dict(_current_round) if _current_round else None


# ── events ───────────────────────────────────────────────────────

def push_event(kind: str, data: dict[str, Any] | None = None, level: str = "info") -> None:
    evt = {
        "id": _event_id(),
        "ts": _now_iso(),
        "kind": kind,
        "level": level,
        "data": data or {},
    }
    with _lock:
        _events.append(evt)


def get_events(after_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        items = list(_events)
    if after_id is not None:
        idx = None
        for i, e in enumerate(items):
            if e["id"] == after_id:
                idx = i
                break
        if idx is None:
            # Unknown cursor: do not replay old events repeatedly.
            return []
        items = items[idx + 1:]
    return items[-limit:]


# ── full snapshot (used by /status) ──────────────────────────────

def get_status_snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "worker_status": _worker_status,
            "entry_paused": _entry_paused,
            "uptime_seconds": round(time.time() - _started_at, 1) if _started_at else 0,
            "stats": dict(_stats),
            "current_round": dict(_current_round) if _current_round else None,
        }
