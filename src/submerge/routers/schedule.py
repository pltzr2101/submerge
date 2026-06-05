"""Auto-merge schedule API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..api import (
    _SCHEDULE_RE,
    _get_schedule_defaults,
    _load_app_settings,
    _load_presets,
    _restart_scheduler,
    _save_app_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/settings/schedule")
def api_get_schedule():
    """Return current auto-merge schedule settings."""
    return _get_schedule_defaults()


@router.post("/api/settings/schedule")
async def api_set_schedule(request: Request):
    """Save auto-merge schedule settings and reconfigure the scheduler.

    Body:
        auto_merge_enabled (bool)
        schedule_time (str, HH:MM)
        run_on_startup (bool)
        schedule_template (str, preset name or "")
    """
    try:
        body = await request.json()
        app_settings = _load_app_settings()

        if "auto_merge_enabled" in body:
            app_settings["auto_merge_enabled"] = bool(body["auto_merge_enabled"])

        if "schedule_time" in body:
            val = str(body["schedule_time"]).strip()
            if val and not _SCHEDULE_RE.match(val):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "status": "error",
                        "message": f"Invalid schedule_time: {val}. Use HH:MM format.",
                    },
                )
            app_settings["schedule_time"] = val or "03:00"

        if "run_on_startup" in body:
            app_settings["run_on_startup"] = bool(body["run_on_startup"])

        if "schedule_template" in body:
            val = str(body["schedule_template"]).strip()
            if val:
                presets = _load_presets()
                if val not in presets:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "status": "error",
                            "message": f"Unknown template: {val}",
                        },
                    )
            app_settings["schedule_template"] = val

        _save_app_settings(app_settings)
        _restart_scheduler()
        logger.info("Schedule settings updated")
        return {"status": "ok", "settings": _get_schedule_defaults()}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Schedule settings error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
