"""Auto-merge scheduler logic and API routes."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..api import (
    _apply_template,
    _get_effective_settings,
    _load_app_settings,
    _load_presets,
    _save_app_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_scheduler: object | None = None
_SCHEDULE_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_schedule_merge_lock: asyncio.Lock | None = None


def _get_schedule_defaults() -> dict[str, Any]:
    """Build schedule settings dict from app settings with defaults."""
    app = _load_app_settings()
    return {
        "auto_merge_enabled": app.get("auto_merge_enabled", False),
        "schedule_time": app.get("schedule_time", "03:00"),
        "run_on_startup": app.get("run_on_startup", False),
        "schedule_template": app.get("schedule_template", ""),
        "repair_before_merge": app.get("repair_before_merge", False),
    }


def _get_schedule_merge_settings():
    """Build SubtoolsSettings for an auto-merge run using the configured template."""

    base = _get_effective_settings()
    defaults = _get_schedule_defaults()
    template = defaults.get("schedule_template", "") or ""
    return _apply_template(base, template)


def start_scheduler(settings, app_settings=None):
    """Start the APScheduler with the configured auto-merge schedule.

    Initializes ``_schedule_merge_lock`` if not already set.

    If apscheduler is not installed, logs a warning and continues.
    """
    global _scheduler, _schedule_merge_lock

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("apscheduler not installed — auto-merge schedule disabled")
        return

    app = app_settings or _load_app_settings()
    if not app.get("auto_merge_enabled", False):
        logger.info("Auto-merge schedule is disabled")
        return

    schedule_time = app.get("schedule_time", "03:00")
    if not _SCHEDULE_RE.match(schedule_time):
        logger.error(f"Invalid schedule_time: {schedule_time}")
        return

    hour, minute = int(schedule_time[:2]), int(schedule_time[3:])

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _execute_scheduled_merge,
        CronTrigger(hour=hour, minute=minute),
        id="auto-merge",
        name="auto-merge",
        replace_existing=True,
    )
    _scheduler.start()

    # Initialize the overlap-prevention lock if not already set.
    if _schedule_merge_lock is None:
        _schedule_merge_lock = asyncio.Lock()

    logger.info(f"Auto-merge scheduler started — daily at {schedule_time}")


def stop_scheduler():
    """Shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        with suppress(Exception):
            _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Auto-merge scheduler stopped")


def restart_scheduler():
    """Stop and restart the scheduler to pick up new settings."""
    stop_scheduler()
    start_scheduler(_get_effective_settings())


async def _execute_scheduled_merge():
    """Target for the scheduled auto-merge job.

    Uses an asyncio.Lock to prevent overlapping executions if a scan
    takes longer than the configured cron interval.
    """
    if _schedule_merge_lock is None:
        logger.warning("Scheduled auto-merge skipped: scheduler not initialized")
        return

    if _schedule_merge_lock.locked():
        logger.warning("Scheduled auto-merge skipped: previous run still in progress")
        return

    async with _schedule_merge_lock:
        from ..repair import repair_all_subtitles_in_root
        from ..routers.scanner import _run_scan

        settings = _get_schedule_merge_settings()
        app_settings = _load_app_settings()
        template = app_settings.get("schedule_template", "") or "(default)"

        if app_settings.get("repair_before_merge", False):
            logger.info("Scheduled repair-before-merge starting …")
            try:
                loop = asyncio.get_running_loop()
                repair_result = await loop.run_in_executor(
                    None,
                    repair_all_subtitles_in_root,
                    settings.media_root,
                )
                logger.info(
                    "Scheduled repair-before-merge done: %d/%d .srt files repaired",
                    repair_result["fixed"],
                    repair_result["total"],
                )
            except Exception as exc:
                logger.error(f"Scheduled repair-before-merge failed: {exc}")

        logger.info(f"Scheduled auto-merge job started (template: {template})")
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, _run_scan, settings)
            logger.info(
                f"Scheduled auto-merge complete: {result['merged']} merged, "
                f"{result['polling']} polling"
            )
        except Exception as exc:
            logger.error(f"Scheduled auto-merge failed: {exc}")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


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

        if "repair_before_merge" in body:
            app_settings["repair_before_merge"] = bool(body["repair_before_merge"])

        _save_app_settings(app_settings)
        restart_scheduler()
        logger.info("Schedule settings updated")
        return {"status": "ok", "settings": _get_schedule_defaults()}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Schedule settings error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
