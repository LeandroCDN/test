from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.bots.bot2.manager import bot2_manager

router = APIRouter(prefix="/worker")


class WorkerAction(BaseModel):
    dry_run: bool = False


@router.get("/status")
def worker_status():
    return {"status": bot2_manager.status}


@router.post("/start")
def start_worker(body: WorkerAction | None = None):
    dry_run = body.dry_run if body else False
    ok, msg = bot2_manager.start(dry_run=dry_run)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"status": bot2_manager.status, "message": msg}


@router.post("/stop")
def stop_worker():
    ok, msg = bot2_manager.stop()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"status": bot2_manager.status, "message": msg}


@router.post("/pause-entry")
def pause_entry():
    ok, msg = bot2_manager.pause_entry()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"entry_paused": True, "message": msg}


@router.post("/resume-entry")
def resume_entry():
    ok, msg = bot2_manager.resume_entry()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"entry_paused": False, "message": msg}


@router.post("/force-redeem")
def force_redeem():
    ok, msg = bot2_manager.force_redeem()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"message": msg}
