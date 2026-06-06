"""Background queue-processing worker thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import SubtoolsSettings

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_worker_stop: threading.Event | None = None


def start_queue_worker(
    settings=None,
    settings_fn: Callable[[], SubtoolsSettings] | None = None,
) -> None:
    """Start the background queue processing worker.

    Args:
        settings: Static settings (used as fallback if settings_fn is not set).
        settings_fn: Callable that returns current effective settings.
                     Use this when settings can change at runtime (e.g. web UI).
    """
    global _worker_thread, _worker_stop

    if _worker_thread is not None and _worker_thread.is_alive():
        return

    _worker_stop = threading.Event()

    def _worker():
        from .config import get_settings
        from .queue import process_queue

        logger.info("Queue worker started")
        while True:
            effective = settings_fn() if settings_fn else (settings or get_settings())
            current_interval = effective.poll_interval
            if _worker_stop.wait(timeout=current_interval):
                break
            try:
                result = process_queue(effective)
                if result["checked"] > 0 or result["merged"] > 0:
                    logger.info(
                        f"Queue worker: checked={result['checked']}, "
                        f"merged={result['merged']}, failed={result['failed']}, "
                        f"pending={result['still_pending']}"
                    )
            except Exception as e:
                logger.error(f"Queue worker error: {e}")
        logger.info("Queue worker stopped")

    _worker_thread = threading.Thread(target=_worker, daemon=True, name="submerge-queue-worker")
    _worker_thread.start()


def stop_queue_worker() -> None:
    """Stop the background queue worker."""
    global _worker_stop
    if _worker_stop is not None:
        _worker_stop.set()
