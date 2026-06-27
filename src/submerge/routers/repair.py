"""Repair API routes — single-track subtitle overlap fixing."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..api import validate_path
from ..merge import InvalidSubtitleError
from ..repair import fix_overlaps_in_file

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/repair/fix-overlaps")
async def api_fix_overlaps(request: Request):
    """Fix overlapping events in a single subtitle file, in-place.

    Expects JSON body::

        {"subtitle_path": "/absolute/path/to/file.srt"}

    Returns::

        {"status": "ok", "repositioned": N, "output_path": "...", "modified": true/false}

    Raises:
        400 if subtitle_path is missing or empty.
        404 if the file does not exist.
        422 if the file cannot be parsed as a subtitle.
        500 for unexpected errors.
    """
    body = await request.json()
    subtitle_path_str = (body.get("subtitle_path") or "").strip()
    if not subtitle_path_str:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "subtitle_path required"},
        )

    try:
        sub_path = validate_path(subtitle_path_str, "subtitle_path", check_media_root=True)
    except HTTPException:
        raise

    try:
        result = fix_overlaps_in_file(sub_path)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail={"status": "error", "message": str(e)}) from e
    except InvalidSubtitleError as e:
        raise HTTPException(status_code=422, detail={"status": "error", "message": str(e)}) from e
    except Exception as e:
        logger.error(f"repair fix-overlaps error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
