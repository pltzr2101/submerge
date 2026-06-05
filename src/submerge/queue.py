"""SQLite-based retry queue for pending subtitle merges.

When a webhook fires but not all languages are present, the merge
is queued. A background worker (driven by FastAPI lifespan) polls
the queue periodically and attempts to complete pending merges.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .config import SubtoolsSettings, get_settings
from .models import QueueEntry

# hook imports are intentionally local (not module-level) to avoid
# circular imports: hook.py imports dequeue/enqueue from queue.py

logger = logging.getLogger(__name__)

_QUEUE_MAX_ENTRIES = 500


def _get_db_path(settings: SubtoolsSettings | None = None) -> Path:
    """Get the SQLite database path."""
    settings = settings or get_settings()
    data_dir = Path(settings.config_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot create config dir {data_dir}: {e}")
    return data_dir / "queue.db"


def _get_connection(
    db_path: Path | None = None,
    settings: SubtoolsSettings | None = None,
) -> sqlite3.Connection | None:
    """Get a connection to the queue database.

    Returns None if the database cannot be opened (e.g., readonly filesystem).
    """
    if db_path is None:
        db_path = _get_db_path(settings)
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    except sqlite3.OperationalError as e:
        logger.warning(f"Queue database unavailable: {e}")
        return None


def init_db(settings: SubtoolsSettings | None = None) -> None:
    """Initialize the queue database (idempotent — safe to call repeatedly).

    Must be called once at startup before any queue operations.
    Called automatically by the FastAPI lifespan handler.
    """
    conn = _get_connection(settings=settings)
    if conn is None:
        return
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_merges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_path TEXT NOT NULL UNIQUE,
                langs_present TEXT NOT NULL DEFAULT '[]',
                langs_missing TEXT NOT NULL DEFAULT '[]',
                first_seen TEXT NOT NULL,
                last_checked TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                error_msg TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON pending_merges(status)
        """)
        # Migration: add duration_ms and output_files columns if they don't exist
        for col, col_type in [
            ("duration_ms", "INTEGER DEFAULT NULL"),
            ("output_files", "TEXT DEFAULT NULL"),
        ]:
            with suppress(sqlite3.OperationalError):
                conn.execute(f"ALTER TABLE pending_merges ADD COLUMN {col} {col_type}")
        conn.commit()
    finally:
        conn.close()


def enqueue(video_path: str | Path, settings: SubtoolsSettings | None = None) -> bool:
    """Add or update a pending merge entry.

    If the entry already exists, updates last_checked and attempt_count.

    Args:
        video_path: Path to the video file
        settings: Configuration

    Returns:
        True if newly inserted, False if updated
    """
    settings = settings or get_settings()
    video_path = str(Path(video_path).resolve())
    video = Path(video_path)
    now = datetime.now(timezone.utc).isoformat()

    from .hook import get_present_and_missing

    present, missing = get_present_and_missing(video, settings)

    conn = _get_connection(settings=settings)
    if conn is None:
        return False
    try:
        # Check if entry exists
        existing = conn.execute(
            "SELECT id, attempt_count FROM pending_merges WHERE video_path = ?",
            (video_path,),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE pending_merges
                   SET langs_present = ?, langs_missing = ?,
                       last_checked = ?, attempt_count = attempt_count + 1
                   WHERE video_path = ?""",
                (json.dumps(present), json.dumps(missing), now, video_path),
            )
            conn.commit()
            logger.debug(f"Queue updated: {video.name} (attempt {existing[1] + 1})")
            return False
        else:
            conn.execute(
                """INSERT INTO pending_merges
                   (video_path, langs_present, langs_missing, first_seen, last_checked, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (video_path, json.dumps(present), json.dumps(missing), now, now),
            )
            conn.commit()
            logger.info(f"Queued: {video.name} (present={present}, missing={missing})")
            return True
    finally:
        conn.close()


def dequeue(
    video_path: str | Path,
    status: Literal["done", "failed"] = "done",
    error_msg: str | None = None,
    duration_ms: int | None = None,
    output_files: list[str] | None = None,
    settings: SubtoolsSettings | None = None,
) -> None:
    """Mark a queue entry as done or failed.

    Args:
        video_path: Path to the video file
        status: "done" or "failed"
        error_msg: Error message if status is "failed"
        duration_ms: Total merge time in milliseconds
        output_files: JSON-encodable list of created output file paths
        settings: Configuration for DB path
    """
    video_path = str(Path(video_path).resolve())
    conn = _get_connection(settings=settings)
    if conn is None:
        return
    try:
        output_json = json.dumps(output_files) if output_files is not None else None
        conn.execute(
            "UPDATE pending_merges SET status = ?, error_msg = ?,"
            " duration_ms = ?, output_files = ? WHERE video_path = ?",
            (status, error_msg, duration_ms, output_json, video_path),
        )
        conn.commit()
        logger.debug(f"Queue entry {status}: {Path(video_path).name}")
    finally:
        conn.close()


def remove_entry(video_path: str | Path, settings: SubtoolsSettings | None = None) -> None:
    """Remove a queue entry entirely."""
    video_path = str(Path(video_path).resolve())
    conn = _get_connection(settings=settings)
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM pending_merges WHERE video_path = ?", (video_path,))
        conn.commit()
    finally:
        conn.close()


def get_video_path_by_id(entry_id: int, settings: SubtoolsSettings | None = None) -> str | None:
    """Return video_path for a queue entry by id, or None if not found."""
    conn = _get_connection(settings=settings)
    if conn is None:
        return None
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT video_path FROM pending_merges WHERE id = ?", (entry_id,)
        ).fetchone()
        return row["video_path"] if row else None
    finally:
        conn.close()


def get_pending_entries(settings: SubtoolsSettings | None = None) -> list[QueueEntry]:
    """Get all pending queue entries."""
    settings = settings or get_settings()
    conn = _get_connection(settings=settings)
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT video_path, langs_present, langs_missing,
                      first_seen, last_checked, attempt_count, status
               FROM pending_merges WHERE status = 'pending'
               ORDER BY first_seen ASC"""
        ).fetchall()
        return [
            QueueEntry(
                video_path=row["video_path"],
                langs_present=json.loads(row["langs_present"]),
                langs_missing=json.loads(row["langs_missing"]),
                first_seen=row["first_seen"],
                last_checked=row["last_checked"],
                attempt_count=row["attempt_count"],
                status=row["status"],
            )
            for row in rows
        ]
    finally:
        conn.close()


def get_all_entries(settings: SubtoolsSettings | None = None) -> list[dict[str, Any]]:
    """Get all queue entries as JSON-serializable dicts."""
    conn = _get_connection(settings=settings)
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""SELECT id, video_path, langs_present, langs_missing,
                      first_seen, last_checked, attempt_count, status, error_msg,
                      duration_ms, output_files
               FROM pending_merges ORDER BY first_seen DESC LIMIT {_QUEUE_MAX_ENTRIES}"""
        ).fetchall()
        return [
            {
                "id": row["id"],
                "video_path": row["video_path"],
                "video_name": Path(row["video_path"]).name,
                "langs_present": json.loads(row["langs_present"]),
                "langs_missing": json.loads(row["langs_missing"]),
                "first_seen": row["first_seen"],
                "last_checked": row["last_checked"],
                "attempt_count": row["attempt_count"],
                "status": row["status"],
                "error_msg": row["error_msg"],
                "duration_ms": row["duration_ms"],
                "output_files": json.loads(row["output_files"]) if row["output_files"] else None,
            }
            for row in rows
        ]
    finally:
        conn.close()


def process_queue(
    settings: SubtoolsSettings | None = None,
    merge_fn: Any = None,
) -> dict[str, int]:
    """Process all pending queue entries.

    Called by the background worker. Attempts to merge for each
    pending entry where all languages are now present.

    Args:
        settings: Configuration
        merge_fn: Optional merge callback (video_path, sub_paths, settings) -> list[Path]
                  Defaults to process_bilingual_merge from hook if not provided.

    Returns:
        Dict with counts: {"checked": N, "merged": N, "failed": N, "still_pending": N}
    """
    if merge_fn is None:
        from .hook import process_bilingual_merge as merge_fn

    settings = settings or get_settings()
    timeout_hours = settings.retry_timeout_h
    pending = get_pending_entries(settings)
    if not pending:
        return {"checked": 0, "merged": 0, "failed": 0, "still_pending": 0}

    checked = 0
    merged = 0
    failed = 0
    still_pending = 0

    for entry in pending:
        checked += 1
        video_path = Path(entry.video_path)

        if not video_path.exists():
            dequeue(video_path, "failed", "Video file no longer exists", settings=settings)
            failed += 1
            continue

        # Check timeout
        try:
            first_seen = datetime.fromisoformat(entry.first_seen)
            elapsed = (datetime.now(timezone.utc) - first_seen).total_seconds() / 3600
            if elapsed > timeout_hours:
                dequeue(video_path, "failed", f"Timed out after {elapsed:.1f}h", settings=settings)
                logger.warning(f"Queue entry timed out: {video_path.name} ({elapsed:.1f}h)")
                failed += 1
                continue
        except (ValueError, TypeError):
            pass

        # Check if all languages are present now
        from .hook import check_all_languages_present, should_skip_existing

        sub_paths = check_all_languages_present(video_path, settings)
        if sub_paths is None:
            # Update the missing/present info
            enqueue(video_path, settings)
            still_pending += 1
            continue

        # Skip if polling worker is already handling this video
        from .hook import get_active_polls

        if str(video_path.resolve()) in get_active_polls():
            still_pending += 1
            continue

        # Try merge
        try:
            if should_skip_existing(video_path, sub_paths, settings):
                dequeue(video_path, "done", settings=settings)
                merged += 1
                continue

            t0 = time.monotonic()
            created = merge_fn(video_path, sub_paths, settings)
            duration_ms = round((time.monotonic() - t0) * 1000)
            created_paths = [str(p) for p in created] if created else []
            dequeue(
                video_path,
                "done",
                duration_ms=duration_ms,
                output_files=created_paths,
                settings=settings,
            )
            merged += 1
            logger.info(f"Queue merge complete: {video_path.name} ({duration_ms}ms)")
        except Exception as e:
            logger.error(f"Queue merge error for {video_path.name}: {e}")
            if entry.attempt_count > 10:
                dequeue(video_path, "failed", str(e), settings=settings)
                failed += 1
            else:
                enqueue(video_path, settings)
                still_pending += 1

    return {
        "checked": checked,
        "merged": merged,
        "failed": failed,
        "still_pending": still_pending,
    }


def get_history(
    limit: int = 200,
    settings: SubtoolsSettings | None = None,
) -> list[dict[str, Any]]:
    """Return the last `limit` completed queue entries (done + failed), newest first.

    Each entry dict contains:
        id, video_path, video_name (basename), status, reason (error_msg),
        duration_ms, output_files (list[str]), created_at (first_seen),
        updated_at (last_checked)
    """
    conn = _get_connection(settings=settings)
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, video_path, first_seen, last_checked,
                      status, error_msg, duration_ms, output_files
               FROM pending_merges
               WHERE status IN ('done', 'failed')
               ORDER BY last_checked DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "video_path": row["video_path"],
                "video_name": Path(row["video_path"]).name,
                "status": row["status"],
                "reason": row["error_msg"],
                "duration_ms": row["duration_ms"],
                "output_files": json.loads(row["output_files"]) if row["output_files"] else [],
                "created_at": row["first_seen"],
                "updated_at": row["last_checked"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def clear_history(settings: SubtoolsSettings | None = None) -> int:
    """Delete all completed (done/failed) entries from the queue table.

    Returns the number of rows removed.
    """
    conn = _get_connection(settings=settings)
    if conn is None:
        return 0
    try:
        cursor = conn.execute("DELETE FROM pending_merges WHERE status IN ('done', 'failed')")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# Background worker management
_worker_thread: threading.Thread | None = None
_worker_stop: threading.Event | None = None


def start_queue_worker(
    settings: SubtoolsSettings | None = None,
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
