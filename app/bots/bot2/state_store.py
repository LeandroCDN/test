"""Thread-safe in-memory state for bot 2."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()
_MAX_EVENTS = 500

_worker_status: str = "stopped"
_entry_paused: bool = False
_stats: dict[str, Any] = {
    "total_rounds": 0,
    "total_entries": 0,
    "total_btc_entries": 0,
    "total_eth_entries": 0,
    "total_sol_entries": 0,
    "total_pnl": 0.0,
    "start_balance": 0.0,
    "current_balance": 0.0,
}
_current_round: dict[str, Any] | None = None
_latest_evaluation: dict[str, Any] | None = None
_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_started_at: float | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_id() -> str:
    return f"{time.time_ns()}"


def reset_state() -> None:
    global _worker_status, _entry_paused, _current_round, _latest_evaluation, _started_at
    with _lock:
        _worker_status = "stopped"
        _entry_paused = False
        _current_round = None
        _latest_evaluation = None
        _started_at = None
        _stats.clear()
        _stats.update(
            {
                "total_rounds": 0,
                "total_entries": 0,
                "total_btc_entries": 0,
                "total_eth_entries": 0,
                "total_sol_entries": 0,
                "total_pnl": 0.0,
                "start_balance": 0.0,
                "current_balance": 0.0,
            }
        )
        _events.clear()


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


def is_entry_paused() -> bool:
    with _lock:
        return _entry_paused


def set_entry_paused(paused: bool) -> None:
    global _entry_paused
    with _lock:
        _entry_paused = paused


def update_stats(patch: dict[str, Any]) -> None:
    with _lock:
        _stats.update(patch)


def get_stats() -> dict[str, Any]:
    with _lock:
        return dict(_stats)


def set_current_round(info: dict[str, Any] | None) -> None:
    global _current_round
    with _lock:
        _current_round = info


def get_current_round() -> dict[str, Any] | None:
    with _lock:
        return dict(_current_round) if _current_round else None


def set_latest_evaluation(info: dict[str, Any] | None) -> None:
    global _latest_evaluation
    with _lock:
        _latest_evaluation = info


def get_latest_evaluation() -> dict[str, Any] | None:
    with _lock:
        return dict(_latest_evaluation) if _latest_evaluation else None


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
        for i, item in enumerate(items):
            if item["id"] == after_id:
                idx = i
                break
        if idx is None:
            return []
        items = items[idx + 1 :]
    return items[-limit:]


def get_status_snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "worker_status": _worker_status,
            "entry_paused": _entry_paused,
            "uptime_seconds": round(time.time() - _started_at, 1) if _started_at else 0,
            "stats": dict(_stats),
            "current_round": dict(_current_round) if _current_round else None,
            "latest_evaluation": dict(_latest_evaluation) if _latest_evaluation else None,
        }
