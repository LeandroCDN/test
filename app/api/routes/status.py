from fastapi import APIRouter
from app.services.state_store import get_status_snapshot, get_stats, get_events

router = APIRouter()


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/status")
def status():
    return get_status_snapshot()


@router.get("/metrics")
def metrics():
    return get_stats()


@router.get("/events")
def events(after: str | None = None, limit: int = 100):
    return get_events(after_id=after, limit=min(limit, 500))
