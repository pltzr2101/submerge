"""Repair API routes — single-track subtitle overlap fixing."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..api import validate_path
from ..exceptions import InvalidSubtitleError
from ..repair import fix_overlaps_in_file, repair_subtitle_paths

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


@router.post("/api/repair/batch-fix-overlaps")
async def api_batch_fix_overlaps(request: Request):
    """Fix overlapping events in multiple subtitle files with a single request.

    Expects JSON body::

        {
            "subtitle_paths": ["/abs/path/one.srt", "/abs/path/two.srt"],
            "exclude_patterns": ["\\\\.custom\\\\.(srt)$"]
        }

    *exclude_patterns* is optional; when omitted the server-side default
    :data:`~submerge.repair.MERGED_OUTPUT_PATTERNS` is used.

    Every path must be an absolute ``.srt`` path within the configured
    media root, otherwise a ``400`` error is returned.

    Returns::

        {
            "status": "ok",
            "total": N,
            "fixed": N,
            "skipped": N,
            "failed": N,
            "repositioned": N
        }
    """
    body = await request.json()
    raw_paths: list = body.get("subtitle_paths") or []

    if not isinstance(raw_paths, list) or len(raw_paths) == 0:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": "subtitle_paths must be a non-empty list of absolute paths",
            },
        )

    exclude_patterns: list[str] | None = body.get("exclude_patterns")
    if exclude_patterns is not None and not isinstance(exclude_patterns, list):
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "exclude_patterns must be a list of strings"},
        )

    validated: list[Path] = []
    for idx, raw in enumerate(raw_paths):
        path_str = str(raw or "").strip()
        if not path_str:
            raise HTTPException(
                status_code=400,
                detail={
                    "status": "error",
                    "message": f"subtitle_paths[{idx}] is empty or missing",
                },
            )
        sub_path = validate_path(path_str, f"subtitle_paths[{idx}]", check_media_root=True)
        if sub_path.suffix.lower() != ".srt":
            raise HTTPException(
                status_code=400,
                detail={
                    "status": "error",
                    "message": f"subtitle_paths[{idx}] must be a .srt file: {sub_path}",
                },
            )
        validated.append(sub_path)

    try:
        result = repair_subtitle_paths(validated, exclude_patterns=exclude_patterns)
    except Exception as e:
        logger.error(f"repair batch-fix-overlaps error: {e}")
        raise HTTPException(
            status_code=500, detail={"status": "error", "message": str(e)}
        ) from e

    logger.info(
        "batch-repair: %d paths → %d fixed, %d skipped, %d failed",
        len(validated),
        result["fixed"],
        result["skipped"],
        result["failed"],
    )
    return {"status": "ok", **result}
