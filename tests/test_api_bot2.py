"""Tests for the isolated bot 2 API."""

from fastapi.testclient import TestClient

from app.api.bot2_main import app
from app.bots.bot2 import state_store as store

client = TestClient(app)


def setup_function():
    store.reset_state()


def test_bot2_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_bot2_status_shape():
    response = client.get("/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["worker_status"] == "stopped"
    assert "stats" in payload


def test_bot2_events_roundtrip():
    store.push_event("test_event", {"bot": 2})
    response = client.get("/events")
    assert response.status_code == 200
    events = response.json()
    assert len(events) == 1
    assert events[0]["kind"] == "test_event"


def test_bot2_pause_requires_running():
    response = client.post("/worker/pause-entry")
    assert response.status_code == 409


def test_bot2_pause_resume_when_running():
    store.set_worker_status("running")

    pause = client.post("/worker/pause-entry")
    assert pause.status_code == 200
    assert store.is_entry_paused() is True

    resume = client.post("/worker/resume-entry")
    assert resume.status_code == 200
    assert store.is_entry_paused() is False
