"""
Tests for the API endpoints and worker lifecycle.

Run:  pytest tests/ -v
"""

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.services import state_store as store
from app.services.bot_manager import bot_manager


@pytest.fixture(autouse=True)
def reset_state():
    """Reset shared state before each test."""
    store.set_worker_status("stopped")
    store.set_entry_paused(False)
    store.update_stats({
        "total_rounds": 0,
        "total_entries": 0,
        "total_btc_entries": 0,
        "total_eth_entries": 0,
        "total_pnl": 0.0,
        "start_balance": 0.0,
        "current_balance": 0.0,
    })
    store.set_current_round(None)
    store._events.clear()
    yield


client = TestClient(app)


# ── Health ───────────────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── Status ───────────────────────────────────────────────────────

def test_status_shape():
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert "worker_status" in body
    assert "stats" in body
    assert body["worker_status"] == "stopped"


# ── Metrics ──────────────────────────────────────────────────────

def test_metrics_returns_stats():
    store.update_stats({"total_entries": 5, "total_pnl": 1.23})
    r = client.get("/metrics")
    assert r.status_code == 200
    data = r.json()
    assert data["total_entries"] == 5
    assert data["total_pnl"] == 1.23


# ── Events ───────────────────────────────────────────────────────

def test_events_empty():
    r = client.get("/events")
    assert r.status_code == 200
    assert r.json() == []


def test_events_push_and_query():
    store.push_event("test_event", {"foo": "bar"})
    store.push_event("another_event", {"x": 1}, level="warn")
    r = client.get("/events")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 2
    assert events[0]["kind"] == "test_event"
    assert events[1]["level"] == "warn"


def test_events_after_filter():
    store.push_event("e1")
    store.push_event("e2")
    store.push_event("e3")
    all_events = client.get("/events").json()
    after_id = all_events[0]["id"]
    r = client.get(f"/events?after={after_id}")
    filtered = r.json()
    assert len(filtered) == 2
    assert filtered[0]["kind"] == "e2"


# ── Worker lifecycle ─────────────────────────────────────────────

def test_stop_when_already_stopped():
    r = client.post("/worker/stop")
    assert r.status_code == 409


def test_pause_when_not_running():
    r = client.post("/worker/pause-entry")
    assert r.status_code == 409


def test_resume_when_not_paused():
    r = client.post("/worker/resume-entry")
    assert r.status_code == 409


def test_force_redeem_when_not_running():
    r = client.post("/worker/force-redeem")
    assert r.status_code == 409


def test_pause_resume_when_running():
    store.set_worker_status("running")
    r = client.post("/worker/pause-entry")
    assert r.status_code == 200
    assert store.is_entry_paused()

    r = client.post("/worker/resume-entry")
    assert r.status_code == 200
    assert not store.is_entry_paused()
