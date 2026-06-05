"""Queue and polling API routes."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..api import _get_effective_settings
from ..hook import (
    check_all_languages_present,
    get_active_polls,
    process_bilingual_merge,
    should_skip_existing,
)
from ..queue import dequeue, get_all_entries, get_video_path_by_id, remove_entry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/polls")
def api_polls():
    """Return list of active polling jobs."""
    return {"polls": get_active_polls()}


@router.get("/api/queue")
def api_queue():
    """Return all queue entries (pending, done, failed)."""
    settings = _get_effective_settings()
    try:
        entries = get_all_entries(settings=settings)
        return JSONResponse({"entries": entries, "count": len(entries)})
    except Exception as e:
        logger.error(f"Queue fetch error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.post("/api/queue/{entry_id}/remove")
def api_queue_remove(entry_id: int):
    """Remove a queue entry by ID."""
    settings = _get_effective_settings()
    video_path = get_video_path_by_id(entry_id, settings=settings)
    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Entry not found"},
        )
    remove_entry(video_path, settings=settings)
    return {"status": "ok"}


@router.post("/api/queue/{entry_id}/retry")
async def api_queue_retry(entry_id: int):
    """Retry a queue entry now."""
    settings = _get_effective_settings()
    video_path = get_video_path_by_id(entry_id, settings=settings)
    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Entry not found"},
        )
    video_path = Path(video_path)
    sub_paths = check_all_languages_present(video_path, settings)
    if sub_paths is None:
        return {"status": "still_waiting", "message": "Not all languages present yet"}

    if should_skip_existing(video_path, sub_paths, settings):
        dequeue(video_path, "done", settings=settings)
        return {"status": "skipped", "reason": "already_exists"}
    created = await run_in_threadpool(process_bilingual_merge, video_path, sub_paths, settings)
    dequeue(video_path, "done", settings=settings)
    return {"status": "merged", "files": [str(f) for f in created]}
