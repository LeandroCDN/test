from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.bot_manager import bot_manager
from app.services.settings_service import AppSettings, load_settings, save_settings, settings_equal

router = APIRouter()


class UpdateSettingsRequest(BaseModel):
    settings: AppSettings


@router.get("/settings")
def get_settings():
    persisted = load_settings()
    active = bot_manager.active_settings
    restart_required = bot_manager.status == "running" and not settings_equal(persisted, active)
    return {
        "safe_mode": True,
        "settings": persisted,
        "active_settings": active,
        "requires_restart": restart_required,
        "note": "Changes are applied only when worker starts.",
    }


@router.put("/settings")
def put_settings(body: UpdateSettingsRequest):
    try:
        saved = save_settings(body.settings.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid settings: {e}")

    active = bot_manager.active_settings
    restart_required = bot_manager.status == "running" and not settings_equal(saved, active)
    return {
        "safe_mode": True,
        "settings": saved,
        "requires_restart": restart_required,
        "message": (
            "Settings saved. Restart worker to apply changes."
            if restart_required
            else "Settings saved."
        ),
    }
