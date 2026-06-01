"""FastAPI API for Bazarr hook integration and Web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import SubtoolsSettings, get_settings
from .hook import (
    InvalidLanguageError,
    ProcessingError,
    check_all_languages_present,
    get_active_polls,
    get_output_path,
    process_hook,
    start_polling,
    find_subtitle_path,
)
from .merge import MergeConfig, merge_bilingual
from .scanner import entry_to_dict, find_videos_needing_merge, scan_directory

# Configure logging for the whole app
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("submerge")

# SSE log queue for streaming to UI
_log_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)


class SSEHandler(logging.Handler):
    """Logging handler that pushes messages to an asyncio queue for SSE streaming."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            try:
                loop = asyncio.get_running_loop()
                # Drop oldest if full (non-blocking)
                if _log_queue.full():
                    try:
                        _log_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                _log_queue.put_nowait(msg)
            except RuntimeError:
                pass  # No event loop running
        except Exception:
            pass


# Install SSE handler on the submerge logger
_sse_handler = SSEHandler()
_sse_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
logging.getLogger("submerge").addHandler(_sse_handler)


class HealthCheckFilter(logging.Filter):
    """Filters out /health request logs to reduce noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "/health" not in message


def setup_logging_filters() -> None:
    """Configure logging filters. Called at startup."""
    health_filter = HealthCheckFilter()
    logging.getLogger("uvicorn.access").addFilter(health_filter)


# Jinja2 templates setup
_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# Runtime-overridable settings (in-memory, not persisted)
_runtime_settings: dict[str, Any] = {}


def _get_effective_settings() -> SubtoolsSettings:
    """Get settings with runtime overrides applied."""
    base = get_settings()
    if not _runtime_settings:
        return base

    # Build overrides dict
    overrides = {}
    for key, val in _runtime_settings.items():
        if key == "pairs":
            overrides["pairs_raw"] = val
        else:
            overrides[key] = val

    # We need to reconstruct settings with overrides
    from .config import get_settings_for_test
    return get_settings_for_test(**overrides)


def _runtime_settings_to_response() -> dict[str, Any]:
    """Return current settings as a response dict."""
    settings = _get_effective_settings()
    return {
        "pairs_raw": settings.pairs_raw,
        "pairs": [f"{b}-{t}" for b, t in settings.pairs],
        "media_root": settings.media_root,
        "poll_interval": settings.poll_interval,
        "color_bottom": settings.color_bottom,
        "color_top": settings.color_top,
        "fontsize": settings.fontsize,
        "layout": settings.layout,
    }


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # Validate config at startup (fail fast)
    settings = get_settings()
    if not settings.pairs:
        raise RuntimeError(
            "SUBTOOLS_PAIRS environment variable is required. "
            "Example: SUBTOOLS_PAIRS='fr-pl,en-pl'"
        )

    setup_logging_filters()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup and shutdown lifecycle."""
        from .queue import init_db, start_queue_worker, stop_queue_worker
        init_db()
        start_queue_worker()
        logger.info("Queue worker started")
        yield
        stop_queue_worker()
        logger.info("Queue worker stopped")

    app = FastAPI(
        title="SubMerge API",
        description="API for automatic bilingual subtitle generation",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()


def validate_path(path_str: str, param_name: str) -> Path:
    """Validate and resolve a path.

    Args:
        path_str: Path to validate
        param_name: Parameter name (for error messages)

    Returns:
        Resolved and validated Path

    Raises:
        HTTPException: If the path is invalid
    """
    try:
        path = Path(path_str)

        # Must be an absolute path
        if not path.is_absolute():
            raise HTTPException(
                status_code=400,
                detail={"status": "error", "message": f"{param_name} must be an absolute path"},
            )

        # Resolve to eliminate .. and symlinks
        return path.resolve()

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Invalid path {param_name}={path_str}: {e}")
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": f"Invalid {param_name} path"},
        )


# =============================================================================
# Web UI Routes
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def ui_index(request: Request):
    """Dashboard page."""
    settings = _get_effective_settings()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "pairs": [f"{b}-{t}" for b, t in settings.pairs],
        "langs": sorted(settings.required_langs),
    })


@app.get("/settings", response_class=HTMLResponse)
async def ui_settings(request: Request):
    """Settings page."""
    settings = _get_effective_settings()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
    })


# =============================================================================
# Bazarr & Lingarr Hooks
# =============================================================================


@app.post("/hook")
def hook(
    video: str = Form(..., description="Path to video file"),
    subtitle: str = Form(..., description="Path to downloaded subtitle"),
    lang: str = Form(..., description="Language code (fr, pl, en)"),
) -> dict:
    """Bazarr post-processing hook."""
    return _handle_hook(video, subtitle, lang, source="bazarr")


@app.post("/lingarr-hook")
def lingarr_hook(
    video: str = Form(..., description="Path to video file"),
    subtitle: str = Form(..., description="Path to translated subtitle"),
    lang: str = Form(..., description="Language code (fr, pl, en)"),
) -> dict:
    """Lingarr post-processing hook.

    Same behavior as /hook but with separate logging for Lingarr events.
    """
    return _handle_hook(video, subtitle, lang, source="lingarr")


def _handle_hook(video: str, subtitle: str, lang: str, source: str) -> dict:
    """Shared hook handler for Bazarr and Lingarr."""
    video_path = validate_path(video, "video")
    subtitle_path = validate_path(subtitle, "subtitle")

    logger.info(f"[{source}] Hook: video={video_path.name}, lang={lang}")

    try:
        result = process_hook(video_path, subtitle_path, lang)

        # Use 200 for all statuses, status is in the body
        response: dict = {"status": result.status}

        if result.files:
            response["files"] = result.files
        if result.present:
            response["present"] = result.present
        if result.missing:
            response["missing"] = result.missing
        if result.reason:
            response["reason"] = result.reason

        return response

    except InvalidLanguageError as e:
        logger.warning(f"Invalid language: {e}")
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": str(e)},
        )
    except ProcessingError as e:
        error_msg = str(e)
        # Don't expose full paths in errors
        if "not found" in error_msg.lower():
            logger.warning(f"File not found: {e}")
            raise HTTPException(
                status_code=400,
                detail={"status": "error", "message": "Video file not found"},
            )
        logger.error(f"Processing error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "Processing failed"},
        )
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "Internal server error"},
        )


@app.get("/health")
def health() -> dict:
    """Health check - verifies that ffmpeg and ffprobe are accessible."""
    ffmpeg_available = shutil.which("ffmpeg") is not None
    ffprobe_available = shutil.which("ffprobe") is not None

    all_ok = ffmpeg_available and ffprobe_available

    return {
        "status": "ok" if all_ok else "degraded",
        "ffmpeg": ffmpeg_available,
        "ffprobe": ffprobe_available,
    }


# =============================================================================
# API Routes for Web UI
# =============================================================================


@app.get("/api/media")
def api_media():
    """Return JSON list of all videos with subtitle status."""
    settings = _get_effective_settings()
    media_root = settings.media_root
    try:
        entries = scan_directory(media_root, settings)
        return JSONResponse([
            entry_to_dict(e, settings) for e in entries
        ])
    except Exception as e:
        logger.error(f"Scan error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


@app.post("/api/merge")
async def api_merge(request: Request):
    """Trigger a merge for a single video."""
    try:
        body = await request.json()
        video_path_str = body.get("video_path", "")
        if not video_path_str:
            raise HTTPException(status_code=400, detail={"status": "error", "message": "video_path required"})

        video_path = validate_path(video_path_str, "video_path")
        settings = _get_effective_settings()

        # Find subtitle paths for all required languages
        from .hook import check_all_languages_present, get_lock_path, process_bilingual_merge, should_skip_existing

        sub_paths = check_all_languages_present(video_path, settings)
        if sub_paths is None:
            # Start polling
            from .hook import get_present_and_missing
            present, missing = get_present_and_missing(video_path, settings)
            start_polling(video_path, settings)
            return {
                "status": "polling",
                "present": present,
                "missing": missing,
                "reason": f"Polling every {settings.poll_interval}s",
            }

        # Check skip
        if should_skip_existing(video_path, sub_paths, settings):
            return {"status": "skipped", "reason": "already_exists"}

        # Run merge in thread to not block
        from .hook import _cancel_polling
        _cancel_polling(video_path)

        created_files = process_bilingual_merge(video_path, sub_paths, settings)
        return {
            "status": "merged",
            "files": [str(f) for f in created_files],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Merge error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


@app.post("/api/sync")
async def api_sync(request: Request):
    """Trigger subtitle synchronization."""
    try:
        body = await request.json()
        subtitle_path_str = body.get("subtitle_path", "")
        lang = body.get("lang", "")

        if not subtitle_path_str:
            raise HTTPException(status_code=400, detail={"status": "error", "message": "subtitle_path required"})

        sub_path = validate_path(subtitle_path_str, "subtitle_path")
        settings = _get_effective_settings()

        if not sub_path.exists():
            raise HTTPException(status_code=400, detail={"status": "error", "message": "Subtitle file not found"})

        # Find reference subtitle (other language from pairs containing this lang)
        video_name_no_lang = sub_path.stem
        video_dir = sub_path.parent

        # Find matching video file for audio sync fallback
        video_file = None
        for ext in (".mkv", ".mp4", ".avi", ".m4v"):
            candidate = sub_path.parent / (sub_path.stem.rsplit(".", 1)[0] + ext)
            if candidate.exists():
                video_file = candidate
                break

        # Try to find reference subtitle in another language
        ref_path = None
        for other_lang in settings.required_langs:
            if other_lang == lang:
                continue
            ref_path = find_subtitle_path(
                Path(sub_path.parent / (sub_path.stem.rsplit(".", 1)[0] + ".mkv")),
                other_lang,
            )
            if ref_path and str(ref_path) != str(sub_path):
                break
            ref_path = None

        # Output path
        output_path = sub_path.parent / f"{sub_path.stem}.synced{sub_path.suffix}"

        try:
            from .sync import sync_subtitles, sync_subtitles_to_video, FfsubsyncNotFoundError, SyncError
        except ImportError:
            return {"status": "error", "message": "ffsubsync not installed. Install: pip install 'submerge[sync]'"}

        try:
            if ref_path:
                result = sync_subtitles(ref_path, sub_path, output_path)
            elif video_file:
                result = sync_subtitles_to_video(video_file, sub_path, output_path)
            else:
                return {"status": "error", "message": "No reference subtitle or video found for sync"}

            return {
                "status": "ok",
                "output": str(result.output_path),
                "offset_ms": result.offset_ms,
            }
        except FfsubsyncNotFoundError:
            return {"status": "error", "message": "ffsubsync not found"}
        except SyncError as e:
            return {"status": "error", "message": str(e)}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


@app.post("/scan")
def api_scan():
    """Scan media directories and start merges for videos needing them."""
    settings = _get_effective_settings()
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
                    start_polling(video_path, settings)
                    polling += 1
                    continue

                from .hook import process_bilingual_merge, should_skip_existing
                if should_skip_existing(video_path, sub_paths, settings):
                    continue

                process_bilingual_merge(video_path, sub_paths, settings)
                merged += 1
            except Exception as e:
                logger.error(f"Scan merge error for {video_path.name}: {e}")

        return {
            "status": "ok",
            "scanned": len(entries),
            "merged": merged,
            "polling": polling,
        }
    except Exception as e:
        logger.error(f"Scan error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


@app.get("/logs/stream")
async def logs_stream():
    """SSE endpoint for streaming log messages."""

    async def event_generator():
        while True:
            try:
                msg = await asyncio.wait_for(_log_queue.get(), timeout=15)
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/polls")
def api_polls():
    """Return list of active polling jobs."""
    return {"polls": get_active_polls()}


@app.get("/api/queue")
def api_queue():
    """Return all queue entries (pending, done, failed)."""
    from .queue import get_all_entries

    settings = _get_effective_settings()
    try:
        entries = get_all_entries(settings=settings)
        return JSONResponse({"entries": entries, "count": len(entries)})
    except Exception as e:
        logger.error(f"Queue fetch error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


@app.post("/api/queue/{entry_id}/remove")
def api_queue_remove(entry_id: int):
    """Remove a queue entry by ID."""
    from .queue import _get_connection, remove_entry

    settings = _get_effective_settings()
    conn = _get_connection(settings=settings)
    if conn is None:
        raise HTTPException(status_code=503, detail={"status": "error", "message": "Queue database unavailable"})
    try:
        row = conn.execute(
            "SELECT video_path FROM pending_merges WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"status": "error", "message": "Entry not found"})
        remove_entry(row[0], settings=settings)
        return {"status": "ok"}
    finally:
        conn.close()


@app.post("/api/queue/{entry_id}/retry")
def api_queue_retry(entry_id: int):
    """Retry a queue entry now."""
    from .queue import _get_connection, dequeue, remove_entry

    settings = _get_effective_settings()
    conn = _get_connection(settings=settings)
    if conn is None:
        raise HTTPException(status_code=503, detail={"status": "error", "message": "Queue database unavailable"})
    try:
        row = conn.execute(
            "SELECT video_path FROM pending_merges WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"status": "error", "message": "Entry not found"})
        video_path = Path(row[0])
        sub_paths = check_all_languages_present(video_path, settings)
        if sub_paths is None:
            return {"status": "still_waiting", "message": "Not all languages present yet"}
        from .hook import process_bilingual_merge, should_skip_existing
        if should_skip_existing(video_path, sub_paths, settings):
            dequeue(video_path, "done", settings=settings)
            return {"status": "skipped", "reason": "already_exists"}
        created = process_bilingual_merge(video_path, sub_paths, settings)
        dequeue(video_path, "done", settings=settings)
        return {"status": "merged", "files": [str(f) for f in created]}
    finally:
        conn.close()


@app.post("/api/settings")
async def api_settings(request: Request):
    """Apply runtime settings (in-memory only, not persisted)."""
    try:
        body = await request.json()

        # Validate and apply each setting
        if "pairs" in body and body["pairs"]:
            pairs_str = str(body["pairs"]).strip()
            if pairs_str:
                from .config import _parse_pairs_string
                try:
                    _parse_pairs_string(pairs_str)
                    _runtime_settings["pairs"] = pairs_str
                except ValueError as e:
                    return {"status": "error", "message": f"Invalid pairs: {e}"}

        if "media_root" in body:
            _runtime_settings["media_root"] = str(body["media_root"])

        if "poll_interval" in body:
            try:
                val = int(body["poll_interval"])
                if 10 <= val <= 3600:
                    _runtime_settings["poll_interval"] = val
            except (ValueError, TypeError):
                pass

        if "color_bottom" in body:
            color = str(body["color_bottom"]).strip()
            if color:
                _runtime_settings["color_bottom"] = color

        if "color_top" in body:
            color = str(body["color_top"]).strip()
            if color:
                _runtime_settings["color_top"] = color

        if "fontsize" in body:
            try:
                val = int(body["fontsize"])
                if 8 <= val <= 72:
                    _runtime_settings["fontsize"] = val
            except (ValueError, TypeError):
                pass

        if "layout" in body:
            layout = str(body["layout"]).strip()
            if layout in ("top-bottom", "stacked"):
                _runtime_settings["layout"] = layout

        logger.info(f"Runtime settings updated: {list(_runtime_settings.keys())}")
        return {"status": "ok", "settings": _runtime_settings_to_response()}

    except Exception as e:
        logger.error(f"Settings update error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})
