"""FastAPI API for Bazarr hook integration and Web UI."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from filelock import FileLock
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from . import __version__
from .config import SubtoolsSettings, get_settings
from .hook import (
    InvalidLanguageError,
    ProcessingError,
    process_hook,
)

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

# Cached reference to the main event loop so threads in run_in_executor can
# still push log messages to the SSE queue via call_soon_threadsafe.
_main_event_loop: asyncio.AbstractEventLoop | None = None

# Per-file asyncio locks to serialize parallel sync calls on the same file
_sync_locks: dict[str, asyncio.Lock] = {}


def _get_sync_lock(path: str) -> asyncio.Lock:
    """Return a per-file asyncio.Lock. Evicts unlocked entries above 200."""
    lock = _sync_locks.setdefault(path, asyncio.Lock())
    if len(_sync_locks) > 200:
        stale = [p for p, lk in list(_sync_locks.items()) if p != path and not lk.locked()]
        for p in stale[:100]:
            evicted = _sync_locks.pop(p, None)
            # Already verified unlocked above; double-check to avoid race
            if evicted is not None and evicted.locked():
                _sync_locks[p] = evicted  # Put it back
    return lock


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
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = _main_event_loop
            if loop is None or not loop.is_running():
                return
            q = _get_log_queue()
            if q.full():
                with suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            loop.call_soon_threadsafe(q.put_nowait, msg)
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
        # Only forward keys that SubtoolsSettings actually accepts — prevents
        # Pydantic ValidationError from stale/unknown keys in _runtime_settings.
        known_fields = set(SubtoolsSettings.model_fields.keys()) | {"pairs_raw"}
        overrides = {
            ("pairs_raw" if k == "pairs" else k): v
            for k, v in _runtime_settings.items()
            if (k == "pairs") or (k in known_fields)
        }
        return SubtoolsSettings.with_overrides(**overrides)


def _apply_template(
    base_settings: SubtoolsSettings,
    template_name: str,
) -> SubtoolsSettings:
    """Return new settings with the named template applied, or base_settings
    if template_name is empty/unknown."""
    if not template_name:
        return base_settings
    presets = _load_presets()
    if template_name not in presets:
        return base_settings
    overrides = dict(presets[template_name])  # shallow copy fixes mutation
    overrides["pairs_raw"] = base_settings.pairs_raw
    known_fields = set(SubtoolsSettings.model_fields.keys())
    overrides = {k: v for k, v in overrides.items() if k in known_fields}
    return SubtoolsSettings.with_overrides(**overrides)


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
        "bottom_fontsize": settings.bottom_fontsize,
        "top_fontsize": settings.top_fontsize,
        "bottom_outline": settings.bottom_outline,
        "bottom_outline_color": settings.bottom_outline_color,
        "top_outline": settings.top_outline,
        "top_outline_color": settings.top_outline_color,
        "layout": settings.layout,
        "notification_url": settings.notification_url,
        "notification_token": "***" if settings.notification_token else "",
    }


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    setup_logging_filters()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup and shutdown lifecycle."""
        global _main_event_loop
        _main_event_loop = asyncio.get_running_loop()

        settings = get_settings()
        if not settings.pairs:
            logger.error(
                "SUBTOOLS_PAIRS is not set! Set it in your docker-compose.yml. "
                "Example: SUBTOOLS_PAIRS=de-ko"
            )
        from .queue import init_db, start_queue_worker, stop_queue_worker

        init_db()
        # Module-level semaphore must be created inside the event loop.
        global _batch_semaphore
        _batch_semaphore = asyncio.Semaphore(4)
        # Ensure locks directory exists
        locks_dir = Path(settings.config_dir) / "locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        start_queue_worker(settings_fn=_get_effective_settings)
        logger.info("Queue worker started")

        # ---- Auto-merge scheduler ----
        # The lock must be initialised inside the event loop
        import submerge.routers.schedule as _sched_mod

        from .routers.schedule import (
            _get_schedule_merge_settings,
            start_scheduler,
            stop_scheduler,
        )

        _sched_mod._schedule_merge_lock = asyncio.Lock()

        app_settings = _load_app_settings()
        if app_settings.get("auto_merge_enabled") and app_settings.get("run_on_startup"):

            async def _startup_merge():
                await asyncio.sleep(10)
                logger.info("Startup auto-merge triggered")
                try:
                    s = _get_schedule_merge_settings()
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, _run_scan, s)
                    logger.info(
                        f"Startup auto-merge complete: {result['merged']} merged, "
                        f"{result['polling']} polling"
                    )
                except Exception as exc:
                    logger.error(f"Startup auto-merge failed: {exc}")

            asyncio.create_task(_startup_merge())

        start_scheduler(settings, app_settings=app_settings)
        # ---------------------------------

        yield

        stop_scheduler()
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
    _rate_limit_request_count: int = 0
    _rate_limit_last_cleanup: float = 0.0

    class RateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            rate_limit_rpm = getattr(_get_effective_settings(), "rate_limit_rpm", 30)
            if rate_limit_rpm <= 0:
                return await call_next(request)

            client = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or (request.client.host if request.client else None)
                or "unknown"
            )
            now = time.monotonic()
            bucket = _rate_limits.get(client, [])
            bucket = [t for t in bucket if now - t < 60]
            bucket.append(now)
            _rate_limits[client] = bucket

            # Periodic cleanup: every 100 requests AND >1000 keys, or every 5 minutes AND >100 keys
            nonlocal _rate_limit_request_count, _rate_limit_last_cleanup
            _rate_limit_request_count += 1
            if (_rate_limit_request_count % 100 == 0 and len(_rate_limits) > 1000) or (
                now - _rate_limit_last_cleanup > 300 and len(_rate_limits) > 100
            ):
                stale = [k for k, v in _rate_limits.items() if not any(now - t < 60 for t in v)]
                for k in stale:
                    del _rate_limits[k]
                _rate_limit_last_cleanup = now

            if len(bucket) > rate_limit_rpm:
                return Response(
                    content=json.dumps({"detail": "Too many requests"}),
                    status_code=429,
                    media_type="application/json",
                )
            return await call_next(request)

    class BasicAuthMiddleware(BaseHTTPMiddleware):
        """HTTP Basic Auth middleware.

        Endpoints in _UNPROTECTED_PREFIXES and _UNPROTECTED_EXACT bypass auth.
        /api/media is intentionally unprotected to allow background polling
        from the UI without requiring credentials on every request.
        Note: DELETE /api/media/merged is NOT in the unprotected list and
        therefore requires authentication when ui_password is set.
        """

        _UNPROTECTED_PREFIXES = ("/health", "/hook", "/lingarr-hook", "/static/")
        _UNPROTECTED_EXACT = {
            "/api/polls",
            "/api/queue",
            "/api/media",  # Media list: intentionally unprotected for background polling
            "/health",
        }

        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if any(path == p or path.startswith(p) for p in self._UNPROTECTED_PREFIXES):
                return await call_next(request)
            if path in self._UNPROTECTED_EXACT:
                return await call_next(request)
            password = getattr(_get_effective_settings(), "ui_password", "")
            if not password:
                return await call_next(request)
            authorization = request.headers.get("Authorization", "")
            if not authorization.startswith("Basic "):
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Submerge"'},
                    content="Authentication required",
                )
            try:
                decoded = base64.b64decode(authorization[6:]).decode()
                provided_user, provided_pass = decoded.split(":", 1)
                expected_user = getattr(_get_effective_settings(), "ui_user", "admin")
                if not hmac.compare_digest(
                    f"{provided_user}:{provided_pass}",
                    f"{expected_user}:{password}",
                ):
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

# Module-level batch merge semaphore — must be created inside the event loop,
# so use a lazy getter. This caps total concurrent batch merge workers across
# all requests, not just per-request.
_batch_semaphore: asyncio.Semaphore | None = None


def _get_batch_semaphore() -> asyncio.Semaphore:
    global _batch_semaphore
    if _batch_semaphore is None:
        logger.warning(
            "_batch_semaphore not initialised in lifespan — "
            "creating lazily (OK in tests, unexpected in production)"
        )
        _batch_semaphore = asyncio.Semaphore(4)
    return _batch_semaphore


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
            settings = _get_effective_settings()
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
        ) from e


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
            "langs": settings.required_langs,
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
        },
    )


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

    video_path = validate_path(video, "video", check_media_root=True)
    subtitle_path = validate_path(subtitle, "subtitle", check_media_root=True)

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
        ) from e
    except ProcessingError as e:
        error_msg = str(e)
        # Don't expose full paths in errors
        if "not found" in error_msg.lower():
            logger.warning(f"File not found: {e}")
            raise HTTPException(
                status_code=400,
                detail={"status": "error", "message": "Video file not found"},
            ) from e
        logger.error(f"Processing error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "Processing failed"},
        ) from e
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "Internal server error"},
        ) from e


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


@app.get("/history", response_class=HTMLResponse)
async def ui_history(request: Request):
    """Merge history page."""
    return templates.TemplateResponse(request, "history.html", {})


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
    from .presets import get_default_presets

    presets = get_default_presets()
    path = _get_presets_path()
    lock_path = path.with_suffix(path.suffix + ".lock")

    with FileLock(str(lock_path), timeout=5):
        if path.exists():
            try:
                custom = json.loads(path.read_text())
                presets.update(custom)
            except Exception:
                pass
    return presets


def _save_custom_presets(presets: dict) -> None:
    from .presets import _DEFAULT_PRESETS

    path = _get_presets_path()
    lock_path = path.with_suffix(path.suffix + ".lock")
    # Only save non-default presets
    custom = {k: v for k, v in presets.items() if k not in _DEFAULT_PRESETS}
    path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(str(lock_path), timeout=5):
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
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=5):
        path.write_text(json.dumps(data, indent=2))


# Include modular routers (imported at end to avoid circular imports)
from .routers.history import router as _history_router  # noqa: E402
from .routers.merge import router as _merge_router  # noqa: E402
from .routers.presets import router as _presets_router  # noqa: E402
from .routers.preview import router as _preview_router  # noqa: E402
from .routers.queue import router as _queue_router  # noqa: E402
from .routers.scanner import _run_scan  # noqa: E402
from .routers.scanner import router as _scanner_router  # noqa: E402
from .routers.schedule import router as _schedule_router  # noqa: E402
from .routers.settings import router as _settings_router  # noqa: E402
from .routers.stats import router as _stats_router  # noqa: E402

app.include_router(_history_router)
app.include_router(_merge_router)
app.include_router(_presets_router)
app.include_router(_preview_router)
app.include_router(_queue_router)
app.include_router(_scanner_router)
app.include_router(_schedule_router)
app.include_router(_settings_router)
app.include_router(_stats_router)
