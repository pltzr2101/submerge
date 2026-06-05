"""Merge and sync API routes."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..api import (
    _apply_template,
    _find_video_for_subtitle,
    _get_batch_semaphore,
    _get_effective_settings,
    _get_sync_lock,
    validate_path,
)
from ..config import SubtoolsSettings
from ..hook import (
    cancel_polling,
    check_all_languages_present,
    find_subtitle_path,
    get_present_and_missing,
    process_bilingual_merge,
    should_skip_existing,
    start_polling,
)
from ..queue import dequeue, enqueue, record_failed
from ..sync import FfsubsyncNotFoundError, SyncError, sync_subtitles, sync_subtitles_to_video

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/merge")
async def api_merge(request: Request):
    """Trigger a merge for a single video.

    Optional JSON fields:
        video_path (required): Path to the video file
        template (optional): Name of a style preset to use
        overwrite (optional): If true, skip the "already_exists" check
    """
    try:
        body = await request.json()
        video_path_str = body.get("video_path", "")
        if not video_path_str:
            raise HTTPException(
                status_code=400, detail={"status": "error", "message": "video_path required"}
            )

        video_path: Path | None = None
        merge_settings: SubtoolsSettings | None = None

        video_path = validate_path(video_path_str, "video_path", check_media_root=True)
        overwrite = body.get("overwrite", False)
        template_name = body.get("template", "").strip()

        settings = _get_effective_settings()
        merge_settings = _apply_template(settings, template_name)

        # Find subtitle paths for all required languages
        sub_paths = check_all_languages_present(video_path, merge_settings)
        if sub_paths is None:
            present, missing = get_present_and_missing(video_path, merge_settings)
            start_polling(video_path, merge_settings)
            return {
                "status": "polling",
                "present": present,
                "missing": missing,
                "reason": f"Polling every {merge_settings.poll_interval}s",
            }

        # Check skip (unless force overwrite)
        if not overwrite and should_skip_existing(video_path, sub_paths, merge_settings):
            return {"status": "skipped", "reason": "already_exists"}

        # Run merge in thread to not block uvicorn worker
        cancel_polling(video_path)

        loop = asyncio.get_running_loop()
        t0 = time.monotonic()
        created_files, quality_warnings = await loop.run_in_executor(
            None,
            lambda: process_bilingual_merge(video_path, sub_paths, merge_settings),
        )
        duration_ms = round((time.monotonic() - t0) * 1000)
        enqueue(video_path, merge_settings)
        dequeue(
            video_path,
            status="done",
            duration_ms=duration_ms,
            output_files=[str(f) for f in created_files],
            settings=merge_settings,
        )
        return {
            "status": "merged",
            "overwrite": overwrite,
            "files": [str(f) for f in created_files],
            "quality_warnings": [
                {"code": w.code, "message": w.message, "severity": w.severity}
                for w in quality_warnings
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Merge error: {e}")
        try:
            if video_path is not None:
                enqueue(video_path, merge_settings)
                dequeue(
                    video_path,
                    status="failed",
                    error_msg=str(e),
                    settings=merge_settings,
                )
        except Exception:
            pass  # History failure must not block the HTTP response
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


def _merge_one_video(
    video_path: Path,
    merge_settings: SubtoolsSettings,
    overwrite: bool,
) -> dict[str, Any]:
    """Check and merge a single video; returns a result dict for batch responses.

    Runs synchronously (intended to be called via ``run_in_executor``).
    """
    try:
        if not video_path.exists():
            return {"video": video_path.name, "status": "error", "reason": "Video file not found"}

        sub_paths = check_all_languages_present(video_path, merge_settings)
        if sub_paths is None:
            present, missing = get_present_and_missing(video_path, merge_settings)
            start_polling(video_path, merge_settings)
            return {"video": video_path.name, "status": "polling", "reason": f"Missing: {missing}"}

        if not overwrite and should_skip_existing(video_path, sub_paths, merge_settings):
            return {"video": video_path.name, "status": "skipped", "reason": "already_exists"}

        t0 = time.monotonic()
        created_files, _ = process_bilingual_merge(video_path, sub_paths, merge_settings)
        duration_ms = round((time.monotonic() - t0) * 1000)
        enqueue(video_path, merge_settings)
        dequeue(
            video_path,
            status="done",
            duration_ms=duration_ms,
            output_files=[str(f) for f in created_files],
            settings=merge_settings,
        )
        return {
            "video": video_path.name,
            "status": "merged",
            "files": [str(f) for f in created_files],
        }
    except Exception as e:
        logger.error(f"Batch re-merge error for {video_path.name}: {e}")
        record_failed(video_path, str(e), settings=merge_settings)
        return {"video": video_path.name, "status": "error", "reason": str(e)}


@router.post("/api/batch-merge")
async def api_batch_merge(request: Request):
    """Trigger re-merge for multiple videos at once.

    Body (JSON):
        video_paths: list[str]   — absolute paths to video files
        template: str            — preset name or "" for effective settings
        overwrite: bool          — if true, skip already_exists check (default true)

    Returns:
        {"results": [{"video": "<name>", "status": "merged"|"skipped"|"error", ...}, ...]}
    """
    try:
        body = await request.json()
        video_paths = body.get("video_paths", [])
        if not video_paths or not isinstance(video_paths, list):
            raise HTTPException(
                status_code=400,
                detail={"status": "error", "message": "video_paths required (list of strings)"},
            )

        template_name = body.get("template", "").strip()
        overwrite = body.get("overwrite", True)

        # Resolve settings (possibly with template)
        base_settings = _get_effective_settings()
        merge_settings = _apply_template(base_settings, template_name)

        async def _merge_one(video_path: Path) -> dict[str, Any]:
            async with _get_batch_semaphore():
                return await asyncio.get_running_loop().run_in_executor(
                    None, _merge_one_video, video_path, merge_settings, overwrite
                )

        tasks = [
            _merge_one(validate_path(p, "video_paths[]", check_media_root=True))
            for p in video_paths
        ]
        results = list(await asyncio.gather(*tasks))

        merged_count = sum(1 for r in results if r["status"] == "merged")
        skipped_count = sum(1 for r in results if r["status"] == "skipped")
        error_count = sum(1 for r in results if r["status"] == "error")
        polling_count = sum(1 for r in results if r["status"] == "polling")
        logger.info(
            f"Batch re-merge: {len(video_paths)} videos — "
            f"{merged_count} merged, {skipped_count} skipped, "
            f"{polling_count} polling, {error_count} errors"
        )

        return {"results": results}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Batch merge error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.post("/api/sync")
async def api_sync(request: Request):
    """Trigger subtitle synchronization with bidirectional pair logic.

    *lang* is the language of the subtitle to synchronize (overwritten in-place).
    The reference language is determined from the configured pairs:
    - If *lang* is a bottom-language in a pair → reference is the top-language
    - If *lang* is a top-language in a pair → reference is the bottom-language
    """
    try:
        body = await request.json()
        subtitle_path_str = body.get("subtitle_path", "")
        lang = body.get("lang", "").strip()

        if not subtitle_path_str:
            raise HTTPException(
                status_code=400,
                detail={"status": "error", "message": "subtitle_path required"},
            )

        sub_path = validate_path(subtitle_path_str, "subtitle_path", check_media_root=True)
        settings = _get_effective_settings()

        if not sub_path.exists():
            raise HTTPException(
                status_code=400,
                detail={"status": "error", "message": "Subtitle file not found"},
            )

        # Determine reference language from configured pairs
        ref_lang: str | None = None
        for bottom, top in settings.pairs:
            if lang == bottom:
                ref_lang = top
                break
            if lang == top:
                ref_lang = bottom
                break

        if ref_lang is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "status": "error",
                    "message": f"Language '{lang}' is not part of any configured pair",
                },
            )

        # Robust video detection: peel language suffixes from the stem
        video_file = _find_video_for_subtitle(sub_path)

        # Try to find reference subtitle
        ref_path: Path | None = None
        if video_file is not None:
            ref_path = find_subtitle_path(video_file, ref_lang)
            if ref_path is not None:
                try:
                    ref_path = validate_path(str(ref_path), "ref_path", check_media_root=True)
                except HTTPException:
                    ref_path = (
                        None  # Referenz außerhalb media_root → ignorieren, auf Video-Sync fallen
                    )
            if ref_path and str(ref_path) == str(sub_path):
                ref_path = None

        if ref_path is None and video_file is None:
            return {
                "status": "error",
                "message": "No reference subtitle or video found for sync",
            }

        # Serialize parallel sync calls on the same file
        async with _get_sync_lock(str(sub_path)):
            try:
                loop = asyncio.get_running_loop()
                if ref_path:
                    result = await loop.run_in_executor(
                        None,
                        sync_subtitles,
                        ref_path,
                        sub_path,
                    )
                else:
                    result = await loop.run_in_executor(
                        None,
                        sync_subtitles_to_video,
                        video_file,
                        sub_path,
                    )
            except FfsubsyncNotFoundError:
                return {"status": "error", "message": "ffsubsync not found"}
            except SyncError as e:
                return {"status": "error", "message": str(e)}

        # Build response — use "warning" status for large-outcome cases
        if result.success is False:
            return {
                "status": "warning",
                "message": "Sync applied but offset is very large (>30s), verify result",
                "output": str(result.output_path),
                "offset_ms": result.offset_ms,
            }

        return {
            "status": "ok",
            "output": str(result.output_path),
            "offset_ms": result.offset_ms,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
