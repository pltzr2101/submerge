"""FastAPI API for Bazarr hook integration and Web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from . import __version__
from .config import SubtoolsSettings, get_settings
from .hook import (
    InvalidLanguageError,
    ProcessingError,
    check_all_languages_present,
    find_subtitle_path,
    get_active_polls,
    process_hook,
    start_polling,
)
from .scanner import entry_to_dict, find_videos_needing_merge, scan_directory

# Configure logging for the whole app
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("submerge")

# SSE log queue for streaming to UI - lazy init (Fix 1: avoid asyncio.Queue outside event loop)
_log_queue: asyncio.Queue[str] | None = None


def _get_log_queue() -> asyncio.Queue[str]:
    """Lazy-initialize the log queue within an active event loop."""
    global _log_queue
    if _log_queue is None:
        _log_queue = asyncio.Queue(maxsize=200)
    return _log_queue


class SSEHandler(logging.Handler):
    """Logging handler that pushes messages to an asyncio queue for SSE streaming."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            try:
                asyncio.get_running_loop()  # ensure running in event loop
                q = _get_log_queue()
                # Drop oldest if full (non-blocking)
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(msg)
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
_runtime_settings_lock = threading.Lock()


def _get_effective_settings() -> SubtoolsSettings:
    """Get settings with runtime overrides applied."""
    base = get_settings()
    with _runtime_settings_lock:
        if not _runtime_settings:
            return base
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
        "bottom_color": settings.bottom_color,
        "top_color": settings.top_color,
        "fontsize": settings.fontsize,
        "layout": settings.layout,
    }


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    setup_logging_filters()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup and shutdown lifecycle."""
        settings = get_settings()
        if not settings.pairs:
            logger.error(
                "SUBTOOLS_PAIRS is not set! Set it in your docker-compose.yml. "
                "Example: SUBTOOLS_PAIRS=de-ko"
            )
        from .queue import init_db, start_queue_worker, stop_queue_worker
        init_db()
        # Ensure locks directory exists
        locks_dir = Path(settings.config_dir) / "locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        start_queue_worker()
        logger.info("Queue worker started")

        # ---- Auto-merge scheduler ----
        app_settings = _load_app_settings()
        if app_settings.get("auto_merge_enabled") and app_settings.get("run_on_startup"):
            async def _startup_merge():
                await asyncio.sleep(10)
                logger.info("Startup auto-merge triggered")
                try:
                    s = _get_schedule_merge_settings()
                    _run_scan(s)
                except Exception as exc:
                    logger.error(f"Startup auto-merge failed: {exc}")

            asyncio.create_task(_startup_merge())

        _start_scheduler(settings, app_settings=app_settings)
        # ---------------------------------

        yield

        _stop_scheduler()
        stop_queue_worker()
        logger.info("Queue worker stopped")

    app = FastAPI(
        title="SubMerge API",
        description="API for automatic bilingual subtitle generation",
        version=__version__,
        lifespan=lifespan,
    )

    # Rate limiting (in-memory, per deployment)
    _rate_limits: dict[str, list[float]] = {}

    class RateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            rate_limit_rpm = getattr(_get_effective_settings(), "rate_limit_rpm", 30)
            if rate_limit_rpm <= 0:
                return await call_next(request)

            client = request.client.host if request.client else "unknown"
            now = time.monotonic()
            bucket = _rate_limits.setdefault(client, [])
            bucket[:] = [t for t in bucket if now - t < 60]
            bucket.append(now)
            _rate_limits[client] = bucket
            if len(bucket) > rate_limit_rpm:
                return Response(
                    content=json.dumps({"detail": "Too many requests"}),
                    status_code=429,
                    media_type="application/json",
                )
            return await call_next(request)

    class BasicAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            # Only protect UI pages, not API/hook endpoints
            if path in ("/", "/settings", "/styles") or path.startswith("/logs"):
                password = getattr(_get_effective_settings(), "ui_password", "")
                if password:
                    authorization = request.headers.get("Authorization", "")
                    if not authorization.startswith("Basic "):
                        return Response(
                            status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Submerge"'},
                            content="Authentication required",
                        )
                    import base64
                    try:
                        decoded = base64.b64decode(authorization[6:]).decode()
                        provided_user, provided_pass = decoded.split(":", 1)
                        expected_user = getattr(_get_effective_settings(), "ui_user", "admin")
                        if provided_user != expected_user or provided_pass != password:
                            return Response(
                                status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Submerge"'},
                                content="Invalid credentials",
                            )
                    except Exception:
                        return Response(
                            status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Submerge"'},
                            content="Invalid credentials",
                        )
            return await call_next(request)

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(BasicAuthMiddleware)

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()


def validate_path(path_str: str, param_name: str, check_media_root: bool = False) -> Path:
    """Validate and resolve a path.

    Args:
        path_str: Path to validate
        param_name: Parameter name (for error messages)
        check_media_root: If True, enforce path is within SUBTOOLS_MEDIA_ROOT

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
        resolved_path = path.resolve()

        # Enforce media_root boundary for user-facing endpoints
        if check_media_root:
            settings = get_settings()
            media_root = Path(settings.media_root).resolve()
            if not resolved_path.is_relative_to(media_root):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "status": "error",
                        "message": f"{param_name} must be within media root ({media_root})",
                    },
                )

        return resolved_path

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Invalid path {param_name}={path_str}: {e}")
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": f"Invalid {param_name} path"},
        )


def _find_video_for_subtitle(sub_path: Path) -> Path | None:
    """Find the video file corresponding to a subtitle file.

    Peels language-code suffixes from the filename stem until a
    matching video file is found. Handles multi-dot filenames like
    'Movie.2024.BluRay.de.hi.srt'.

    Args:
        sub_path: Path to subtitle file

    Returns:
        Path to video file or None
    """
    video_exts = (".mkv", ".mp4", ".avi", ".m4v")
    stem = sub_path.stem

    # Keep peeling suffixes until find a video or no dots left
    while "." in stem:
        for ext in video_exts:
            candidate = sub_path.parent / (stem + ext)
            if candidate.exists():
                return candidate
        stem = stem.rsplit(".", 1)[0]

    # Last try: the stem as-is
    for ext in video_exts:
        candidate = sub_path.parent / (stem + ext)
        if candidate.exists():
            return candidate

    return None


# =============================================================================
# Web UI Routes
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def ui_index(request: Request):
    """Dashboard page."""
    settings = _get_effective_settings()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "pairs": [f"{b}-{t}" for b, t in settings.pairs],
            "langs": sorted(settings.required_langs),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def ui_settings(request: Request):
    """Settings page."""
    settings = _get_effective_settings()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "settings": settings,
        },
    )


@app.get("/styles", response_class=HTMLResponse)
async def ui_styles(request: Request):
    """Style editor page."""
    settings = _get_effective_settings()
    pairs = settings.pairs
    lang_bottom = pairs[0][0] if pairs else "de"
    lang_top = pairs[0][1] if pairs else "ko"
    app_settings = _load_app_settings()
    default_template = app_settings.get("default_template", "")
    return templates.TemplateResponse(
        request,
        "styles.html",
        {
            "lang_bottom": lang_bottom,
            "lang_top": lang_top,
            "default_template": default_template,
            "config": {
            "bottom_fontsize": settings.bottom_fontsize,
            "bottom_color": settings.bottom_color,
            "bottom_outline_color": settings.bottom_outline_color,
            "bottom_outline": settings.bottom_outline,
            "bottom_shadow": settings.bottom_shadow,
            "bottom_bold": settings.bottom_bold,
            "bottom_margin_v": settings.bottom_margin_v,
            "bottom_margin_h": settings.bottom_margin_h,
            "bottom_spacing": settings.bottom_spacing,
            "font_bottom": settings.font_bottom,
            "top_fontsize": settings.top_fontsize,
            "top_color": settings.top_color,
            "top_outline_color": settings.top_outline_color,
            "top_outline": settings.top_outline,
            "top_shadow": settings.top_shadow,
            "top_bold": settings.top_bold,
            "top_margin_v": settings.top_margin_v,
            "top_margin_h": settings.top_margin_h,
            "top_spacing": settings.top_spacing,
            "font_top": settings.font_top,
            "layout": settings.layout,
            "stacked_gap": settings.stacked_gap,
        },
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
    settings = _get_effective_settings()
    if not settings.pairs:
        raise HTTPException(
            status_code=503,
            detail={"status": "error", "message": "SUBTOOLS_PAIRS not configured"},
        )

    video_path = validate_path(video, "video")
    subtitle_path = validate_path(subtitle, "subtitle")

    logger.info(f"[{source}] Hook: video={video_path.name}, lang={lang}")

    try:
        result = process_hook(video_path, subtitle_path, lang, settings=settings)

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
    """Health check - verifies ffmpeg/ffprobe and configuration."""
    ffmpeg_available = shutil.which("ffmpeg") is not None
    ffprobe_available = shutil.which("ffprobe") is not None
    settings = _get_effective_settings()
    configured = bool(settings.pairs)

    all_ok = ffmpeg_available and ffprobe_available and configured

    return {
        "status": "ok" if all_ok else "degraded",
        "ffmpeg": ffmpeg_available,
        "ffprobe": ffprobe_available,
        "configured": configured,
        "pairs": [f"{b}-{t}" for b, t in settings.pairs],
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
            raise HTTPException(status_code=400, detail={"status": "error", "message": "video_path required"})  # noqa: E501

        video_path = validate_path(video_path_str, "video_path")
        overwrite = body.get("overwrite", False)
        template_name = body.get("template", "").strip()

        settings = _get_effective_settings()
        # Apply template styles if specified
        merge_settings = settings
        if template_name:
            presets = _load_presets()
            if template_name in presets:
                from .config import get_settings_for_test
                overrides = presets[template_name]
                overrides["pairs_raw"] = settings.pairs_raw
                # Defense-in-depth: filter out unknown keys (e.g. UI-only fields)
                known_fields = set(SubtoolsSettings.model_fields.keys())
                overrides = {k: v for k, v in overrides.items() if k in known_fields}
                merge_settings = get_settings_for_test(**overrides)

        # Find subtitle paths for all required languages
        from .hook import check_all_languages_present, process_bilingual_merge, should_skip_existing

        sub_paths = check_all_languages_present(video_path, merge_settings)
        if sub_paths is None:
            from .hook import get_present_and_missing
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
        from .hook import _cancel_polling
        _cancel_polling(video_path)

        loop = asyncio.get_running_loop()
        created_files = await loop.run_in_executor(
            None,
            lambda: process_bilingual_merge(video_path, sub_paths, merge_settings),
        )
        return {
            "status": "merged",
            "overwrite": overwrite,
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
            raise HTTPException(status_code=400, detail={"status": "error", "message": "subtitle_path required"})  # noqa: E501

        sub_path = validate_path(subtitle_path_str, "subtitle_path")
        settings = _get_effective_settings()

        if not sub_path.exists():
            raise HTTPException(status_code=400, detail={"status": "error", "message": "Subtitle file not found"})  # noqa: E501

        # Robust video detection: peel language suffixes from the stem
        video_file = _find_video_for_subtitle(sub_path)
        if video_file is None:
            # Fallback: try the old rsplit method
            for ext in (".mkv", ".mp4", ".avi", ".m4v"):
                candidate = sub_path.parent / (sub_path.stem.rsplit(".", 1)[0] + ext)
                if candidate.exists():
                    video_file = candidate
                    break

        # Try to find reference subtitle in another language
        ref_path = None
        if video_file is not None:
            for other_lang in settings.required_langs:
                if other_lang == lang:
                    continue
                ref_path = find_subtitle_path(video_file, other_lang)
                if ref_path and str(ref_path) != str(sub_path):
                    break
                ref_path = None

        # Output path
        output_path = sub_path.parent / f"{sub_path.stem}.synced{sub_path.suffix}"

        try:
            from .sync import (
                FfsubsyncNotFoundError,
                SyncError,
                sync_subtitles,
                sync_subtitles_to_video,
            )
        except ImportError:
            return {"status": "error", "message": "ffsubsync not installed. Install: pip install 'submerge[sync]'"}  # noqa: E501

        try:
            if ref_path:
                result = sync_subtitles(ref_path, sub_path, output_path)
            elif video_file:
                result = sync_subtitles_to_video(video_file, sub_path, output_path)
            else:
                return {"status": "error", "message": "No reference subtitle or video found for sync"}  # noqa: E501

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
def api_scan(background_tasks: BackgroundTasks):
    """Scan media directories and start merges for videos needing them.

    Runs in background to avoid blocking the request thread.
    Progress is logged and visible via /logs/stream.
    """
    settings = _get_effective_settings()
    background_tasks.add_task(_run_scan, settings)
    return {"status": "started", "message": "Scan running in background, see /logs/stream for progress"}  # noqa: E501


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

        logger.info(f"Scan complete: scanned={len(entries)}, merged={merged}, polling={polling}")
        return {"status": "ok", "scanned": len(entries), "merged": merged, "polling": polling}
    except Exception as e:
        logger.exception(f"Scan error: {e}")
        raise


@app.get("/logs/stream")
async def logs_stream():
    """SSE endpoint for streaming log messages."""

    async def event_generator():
        q = _get_log_queue()
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=15)
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
    from .queue import get_video_path_by_id, remove_entry

    settings = _get_effective_settings()
    video_path = get_video_path_by_id(entry_id, settings=settings)
    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Entry not found"},
        )
    remove_entry(video_path, settings=settings)
    return {"status": "ok"}


@app.post("/api/queue/{entry_id}/retry")
async def api_queue_retry(entry_id: int):
    """Retry a queue entry now."""
    from .queue import dequeue, get_video_path_by_id

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
    from .hook import process_bilingual_merge, should_skip_existing
    if should_skip_existing(video_path, sub_paths, settings):
        dequeue(video_path, "done", settings=settings)
        return {"status": "skipped", "reason": "already_exists"}
    created = await run_in_threadpool(
        process_bilingual_merge, video_path, sub_paths, settings
    )
    dequeue(video_path, "done", settings=settings)
    return {"status": "merged", "files": [str(f) for f in created]}


@app.post("/api/settings")
async def api_settings(request: Request):
    """Apply runtime settings (in-memory only, not persisted)."""
    try:
        body = await request.json()

        with _runtime_settings_lock:
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

            if "bottom_color" in body:
                color = str(body["bottom_color"]).strip()
                if color:
                    _runtime_settings["bottom_color"] = color

            if "top_color" in body:
                color = str(body["top_color"]).strip()
                if color:
                    _runtime_settings["top_color"] = color

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


# =============================================================================
# Style Presets
# =============================================================================

_DEFAULT_PRESETS = {
    "Standard": {
        "bottom_fontsize": 20, "bottom_color": "#FFFFFF", "bottom_outline_color": "#000000",
        "bottom_outline": 2, "bottom_shadow": 1, "bottom_bold": False,
        "bottom_margin_v": 30, "bottom_margin_h": 20, "bottom_spacing": 0, "font_bottom": "",
        "top_fontsize": 18, "top_color": "#FFD700", "top_outline_color": "#000000",
        "top_outline": 2, "top_shadow": 1, "top_bold": False,
        "top_margin_v": 15, "top_margin_h": 20, "top_spacing": 0, "font_top": "Noto Sans KR",
        "layout": "top-bottom", "stacked_gap": 8,
    },
    "Cinema Dark": {
        "bottom_fontsize": 22, "bottom_color": "#FFFFFF", "bottom_outline_color": "#000000",
        "bottom_outline": 3, "bottom_shadow": 2, "bottom_bold": False,
        "bottom_margin_v": 40, "bottom_margin_h": 30, "bottom_spacing": 0, "font_bottom": "",
        "top_fontsize": 16, "top_color": "#FFD700", "top_outline_color": "#000000",
        "top_outline": 3, "top_shadow": 2, "top_bold": False,
        "top_margin_v": 10, "top_margin_h": 30, "top_spacing": 0, "font_top": "Noto Sans KR",
        "layout": "top-bottom", "stacked_gap": 10,
    },
    "Bright": {
        "bottom_fontsize": 18, "bottom_color": "#FFFF00", "bottom_outline_color": "#0000FF",
        "bottom_outline": 1, "bottom_shadow": 0, "bottom_bold": True,
        "bottom_margin_v": 20, "bottom_margin_h": 15, "bottom_spacing": 0, "font_bottom": "",
        "top_fontsize": 16, "top_color": "#00FF00", "top_outline_color": "#0000FF",
        "top_outline": 1, "top_shadow": 0, "top_bold": True,
        "top_margin_v": 10, "top_margin_h": 15, "top_spacing": 0, "font_top": "Noto Sans KR",
        "layout": "stacked", "stacked_gap": 12,
    },
}


def _get_config_dir() -> Path:
    """Return the config directory for submerge."""
    settings = _get_effective_settings()
    return Path(settings.config_dir)


def _get_presets_path() -> Path:
    return _get_config_dir() / "style_presets.json"


def _get_settings_path() -> Path:
    """Path to settings.json in the config directory."""
    return _get_config_dir() / "settings.json"


def _load_presets() -> dict:
    presets = dict(_DEFAULT_PRESETS)
    path = _get_presets_path()
    if path.exists():
        try:
            custom = json.loads(path.read_text())
            presets.update(custom)
        except Exception:
            pass
    return presets


def _save_custom_presets(presets: dict) -> None:
    path = _get_presets_path()
    # Only save non-default presets
    custom = {k: v for k, v in presets.items() if k not in _DEFAULT_PRESETS}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(custom, indent=2))


def _load_app_settings() -> dict[str, Any]:
    """Load application settings from settings.json."""
    path = _get_settings_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _save_app_settings(data: dict[str, Any]) -> None:
    """Save application settings to settings.json."""
    path = _get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------
# Scheduler for auto-merge jobs
# ---------------------------------------------------------------

_scheduler: object | None = None
_SCHEDULE_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _get_schedule_defaults() -> dict[str, Any]:
    """Build schedule settings dict from app settings with defaults."""
    app = _load_app_settings()
    return {
        "auto_merge_enabled": app.get("auto_merge_enabled", False),
        "schedule_time": app.get("schedule_time", "03:00"),
        "run_on_startup": app.get("run_on_startup", False),
        "schedule_template": app.get("schedule_template", ""),
    }


def _get_schedule_merge_settings() -> SubtoolsSettings:
    """Build SubtoolsSettings for an auto-merge run using the configured template."""
    from .config import get_settings_for_test  # noqa: F811

    base = _get_effective_settings()
    app = _load_app_settings()
    template = app.get("schedule_template", "") or app.get("default_template", "")

    if template:
        presets = _load_presets()
        if template in presets:
            overrides = dict(presets[template])
            overrides["pairs_raw"] = base.pairs_raw

            # Defense-in-depth: filter out unknown keys (e.g. UI-only fields)
            known_fields = set(SubtoolsSettings.model_fields.keys())
            overrides = {k: v for k, v in overrides.items() if k in known_fields}

            return get_settings_for_test(**overrides)
    return base


def _start_scheduler(
    settings: SubtoolsSettings, app_settings: dict[str, Any] | None = None
) -> None:
    """Start the APScheduler with the configured auto-merge schedule.

    If apscheduler is not installed, logs a warning and continues.
    """
    global _scheduler

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
    logger.info(f"Auto-merge scheduler started — daily at {schedule_time}")


def _stop_scheduler() -> None:
    """Shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass  # Event loop may already be closed
        _scheduler = None
        logger.info("Auto-merge scheduler stopped")


def _restart_scheduler() -> None:
    """Stop and restart the scheduler to pick up new settings."""
    _stop_scheduler()
    _start_scheduler(_get_effective_settings())


async def _execute_scheduled_merge() -> None:
    """Target for the scheduled auto-merge job."""
    settings = _get_schedule_merge_settings()
    template = _load_app_settings().get("schedule_template", "") or "(default)"
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


@app.get("/api/presets")
def api_presets_list():
    """List all available style presets (built-in + custom)."""
    presets = _load_presets()
    return {"presets": [{"name": k} for k in sorted(presets.keys())]}


@app.get("/api/presets/{name}")
def api_presets_get(name: str):
    """Get the style fields for a specific preset."""
    presets = _load_presets()
    if name not in presets:
        raise HTTPException(status_code=404, detail={"status": "error", "message": "Preset not found"})  # noqa: E501
    return {"name": name, "styles": presets[name]}


@app.post("/api/presets")
async def api_presets_save(request: Request):
    """Save a new custom style preset."""
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        styles = body.get("styles", {})
        if not name:
            raise HTTPException(status_code=400, detail={"status": "error", "message": "Name required"})  # noqa: E501
        if name in _DEFAULT_PRESETS:
            raise HTTPException(status_code=400, detail={"status": "error", "message": "Cannot override built-in preset"})  # noqa: E501
        presets = _load_presets()
        presets[name] = styles
        _save_custom_presets(presets)
        return {"status": "ok", "name": name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


@app.delete("/api/presets/{name}")
def api_presets_delete(name: str):
    """Delete a custom style preset (built-in presets cannot be deleted)."""
    if name in _DEFAULT_PRESETS:
        raise HTTPException(status_code=400, detail={"status": "error", "message": "Cannot delete built-in preset"})  # noqa: E501

    presets = _load_presets()
    if name not in presets:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Preset not found"},
        )

    # Prevent deleting the currently active default template
    app_settings = _load_app_settings()
    default_template = app_settings.get("default_template", "")
    if name == default_template:
        raise HTTPException(status_code=400, detail={"status": "error", "message": "Cannot delete the active default template"})  # noqa: E501

    del presets[name]
    _save_custom_presets(presets)
    return {"status": "ok", "deleted": name}


@app.get("/api/settings/default-template")
def api_get_default_template():
    """Get the current default style template name."""
    app_settings = _load_app_settings()
    return {"default_template": app_settings.get("default_template", "")}


@app.post("/api/settings/default-template")
async def api_set_default_template(request: Request):
    """Set the default style template name."""
    try:
        body = await request.json()
        name = body.get("template", "").strip()
        if name:
            presets = _load_presets()
            if name not in presets:
                raise HTTPException(
                    status_code=400,
                    detail={"status": "error", "message": f"Unknown template: {name}"},
                )
        app_settings = _load_app_settings()
        if name:
            app_settings["default_template"] = name
        elif "default_template" in app_settings:
            del app_settings["default_template"]
        _save_app_settings(app_settings)
        return {"status": "ok", "default_template": name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


@app.get("/api/settings/schedule")
def api_get_schedule():
    """Return current auto-merge schedule settings."""
    return _get_schedule_defaults()


@app.post("/api/settings/schedule")
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
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


@app.delete("/api/media/merged")
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
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})


# =============================================================================
# Frame Extraction
# =============================================================================


@app.get("/api/frame-extract")
def api_frame_extract(video_path: str, timestamp_s: int = 30):
    """Extract a single frame from a video file via ffmpeg.

    Args:
        video_path: Absolute path to video file
        timestamp_s: Timestamp in seconds (default 30)

    Returns:
        JPEG image bytes
    """
    import subprocess
    import tempfile

    video = validate_path(video_path, "video_path")
    if not video.exists():
        raise HTTPException(status_code=400, detail={"status": "error", "message": "Video not found"})  # noqa: E501

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        cmd = [
            "ffmpeg", "-y", "-ss", str(timestamp_s),
            "-i", str(video), "-vframes", "1",
            "-q:v", "2", tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not Path(tmp_path).exists():
            raise HTTPException(status_code=500, detail={"status": "error", "message": "Frame extraction failed"})  # noqa: E501

        from fastapi.responses import FileResponse
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
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)})
