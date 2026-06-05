"""Media scanner, frame extraction, and merged-file deletion API routes."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from ..api import (
    _apply_template,
    _get_batch_semaphore,
    _get_effective_settings,
    _load_app_settings,
    validate_path,
)
from ..config import SubtoolsSettings
from ..hook import (
    find_subtitle_path,
    process_bilingual_merge,
    should_skip_existing,
    start_polling,
)
from ..scanner import entry_to_dict, find_videos_needing_merge, scan_directory

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/media")
async def api_media():
    """Return JSON list of all videos with subtitle status."""
    settings = _get_effective_settings()
    media_root = settings.media_root
    try:
        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(
            None, lambda: list(scan_directory(media_root, settings))
        )
        return JSONResponse([entry_to_dict(e, settings) for e in entries])
    except Exception as e:
        logger.error(f"Scan error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.post("/scan")
def api_scan(background_tasks: BackgroundTasks):
    """Scan media directories and start merges for videos needing them.

    Runs in background to avoid blocking the request thread.
    Progress is logged and visible via /logs/stream.
    """
    settings = _get_effective_settings()
    app_settings = _load_app_settings()
    scan_settings = _apply_template(
        settings,
        app_settings.get("schedule_template", "") or app_settings.get("default_template", ""),
    )
    background_tasks.add_task(_scan_background, scan_settings)
    return {
        "status": "started",
        "message": "Scan running in background, see /logs/stream for progress",
    }


async def _scan_background(settings: SubtoolsSettings) -> dict:
    """Async wrapper that protects _run_scan with the batch semaphore and thread pool."""
    loop = asyncio.get_running_loop()
    async with _get_batch_semaphore():
        return await loop.run_in_executor(None, _run_scan, settings)


def _run_scan(settings: SubtoolsSettings) -> dict:
    """Execute the scan merge operation (runs in background task)."""
    try:
        entries = find_videos_needing_merge(settings.media_root, settings)
        merged = 0
        polling = 0

        for entry in entries:
            video_path = Path(entry.video_path)
            try:
                sub_paths = {}
                for lang in settings.required_langs:
                    p = find_subtitle_path(video_path, lang)
                    if p:
                        sub_paths[lang] = p

                if len(sub_paths) != len(settings.required_langs):
                    missing_langs = [
                        lang for lang in settings.required_langs if lang not in sub_paths
                    ]
                    logger.info(
                        f"Scan {video_path.name}: missing {missing_langs}, starting polling"
                    )
                    start_polling(video_path, settings)
                    polling += 1
                    continue

                if should_skip_existing(video_path, sub_paths, settings):
                    continue

                process_bilingual_merge(video_path, sub_paths, settings)
                merged += 1
            except Exception as e:
                logger.error(f"Scan merge error for {video_path.name}: {e}")

        logger.info(f"Scan complete: scanned={len(entries)}, merged={merged}, polling={polling}")
        return {"status": "ok", "scanned": len(entries), "merged": merged, "polling": polling}
    except Exception as e:
        logger.exception(f"Scan error: {e}")
        raise


@router.delete("/api/media/merged")
async def api_delete_merged(request: Request):
    """Delete merged subtitle (.ass) files for a video. Only removes the merged file,
    never touches the original .srt source files."""
    try:
        body = await request.json()
        video_path_str = body.get("video_path", "")
        if not video_path_str:
            raise HTTPException(
                status_code=400,
                detail={"status": "error", "message": "video_path required"},
            )

        video_path = validate_path(video_path_str, "video_path", check_media_root=True)
        settings = _get_effective_settings()

        deleted = []
        for lang_bottom, lang_top in settings.pairs:
            pair_key = f"{lang_bottom}-{lang_top}"
            merged_file = video_path.parent / f"{video_path.stem}.{pair_key}.ass"
            if merged_file.exists():
                merged_file.unlink()
                deleted.append(str(merged_file))
                logger.info(f"Deleted merged subtitle: {merged_file}")

        if not deleted:
            return {"status": "ok", "message": "No merged files found to delete", "deleted": []}

        return {"status": "ok", "deleted": deleted}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Delete merged error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.get("/api/frame-extract")
async def api_frame_extract(video_path: str, timestamp_s: int = 30):
    """Extract a single frame from a video file via ffmpeg.

    Args:
        video_path: Absolute path to video file
        timestamp_s: Timestamp in seconds (default 30)

    Returns:
        JPEG image bytes
    """
    video = validate_path(video_path, "video_path", check_media_root=True)
    if not video.exists():
        raise HTTPException(
            status_code=400, detail={"status": "error", "message": "Video not found"}
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp_s),
            "-i",
            str(video),
            "-vframes",
            "1",
            "-q:v",
            "2",
            tmp_path,
        ]

        def _run_ffmpeg():
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _run_ffmpeg)

        if result.returncode != 0 or not Path(tmp_path).exists():
            raise HTTPException(
                status_code=500, detail={"status": "error", "message": "Frame extraction failed"}
            )

        return FileResponse(
            tmp_path,
            media_type="image/jpeg",
            background=BackgroundTask(Path(tmp_path).unlink, missing_ok=True),
        )

    except HTTPException:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        raise
    except Exception as e:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        logger.exception(f"Frame extraction error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
