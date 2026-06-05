"""Statistics API route — aggregate queue statistics."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..api import _get_effective_settings
from ..queue import get_stats

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/stats")
def api_stats():
    """Return aggregate merge statistics from the queue database.

    Returns JSON with: total_merged, total_failed, total_pending,
    success_rate, avg_retries, oldest_pending_hours, generated_at.
    """
    try:
        settings = _get_effective_settings()
        stats = get_stats(settings=settings)
        return stats
    except Exception as e:
        logger.error(f"Stats error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
