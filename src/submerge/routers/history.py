"""History API routes — completed merge entries."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..api import _get_effective_settings
from ..queue import clear_history, get_history

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/history")
def api_history(limit: int = 200):
    """Return completed merge history entries, newest first."""
    settings = _get_effective_settings()
    try:
        entries = get_history(limit=limit, settings=settings)
        return JSONResponse({"entries": entries, "count": len(entries)})
    except Exception as e:
        logger.error(f"History fetch error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.post("/api/history/clear")
def api_history_clear():
    """Delete all completed (done/failed) entries from the queue table."""
    settings = _get_effective_settings()
    try:
        count = clear_history(settings=settings)
        logger.info(f"History cleared: {count} entries removed")
        return {"status": "ok", "removed": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
