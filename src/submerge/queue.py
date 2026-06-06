"""SQLite-based retry queue for pending subtitle merges.

When a webhook fires but not all languages are present, the merge
is queued. A background worker (driven by FastAPI lifespan) polls
the queue periodically and attempts to complete pending merges.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
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
            # Enforce queue size limit: reject new entries if pending
            # count already exceeds _QUEUE_MAX_ENTRIES, logging a warning
            # so operators can tune the limit or clear the queue.
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM pending_merges WHERE status = 'pending'"
            ).fetchone()[0]
            if pending_count >= _QUEUE_MAX_ENTRIES:
                logger.warning(
                    "Queue full (%d pending entries — limit is %d). "
                    "New entry for %s rejected. Clear done/failed entries "
                    "or raise _QUEUE_MAX_ENTRIES.",
                    pending_count,
                    _QUEUE_MAX_ENTRIES,
                    video.name,
                )
                return False

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


def record_failed(
    video_path: str | Path,
    error_msg: str,
    settings: SubtoolsSettings | None = None,
) -> None:
    """Insert or update a queue entry directly as 'failed'.

    Unlike enqueue()+dequeue(), this is a single atomic write that never
    transitions through 'pending', avoiding race conditions with the
    background queue worker.

    Args:
        video_path: Path to the video file
        error_msg: Error description for the history/log
        settings: Configuration for DB path
    """
    video_path = str(Path(video_path).resolve())
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection(settings=settings)
    if conn is None:
        return
    try:
        conn.execute(
            """INSERT INTO pending_merges
               (video_path, langs_present, langs_missing, first_seen, last_checked,
                attempt_count, status, error_msg)
               VALUES (?, '[]', '[]', ?, ?, 1, 'failed', ?)
               ON CONFLICT(video_path) DO UPDATE SET
                   status = 'failed',
                   error_msg = excluded.error_msg,
                   last_checked = excluded.last_checked,
                   attempt_count = attempt_count + 1""",
            (video_path, now, now, error_msg),
        )
        conn.commit()
        logger.debug(f"Recorded failed: {Path(video_path).name}")
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


def get_all_entries(settings: SubtoolsSettings | None = None) -> dict[str, Any]:
    """Get queue entries as JSON-serializable dict with truncation info.

    Returns:
        Dict with keys:
            entries: list of entry dicts (max _QUEUE_MAX_ENTRIES)
            total: total number of entries in the DB
            truncated: True if total > _QUEUE_MAX_ENTRIES
    """
    conn = _get_connection(settings=settings)
    if conn is None:
        return {"entries": [], "total": 0, "truncated": False}
    try:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM pending_merges").fetchone()[0]
        rows = conn.execute(
            """SELECT id, video_path, langs_present, langs_missing,
                      first_seen, last_checked, attempt_count, status, error_msg,
                      duration_ms, output_files
               FROM pending_merges ORDER BY first_seen DESC LIMIT ?""",
            (_QUEUE_MAX_ENTRIES,),
        ).fetchall()
        entries = [
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
        return {
            "entries": entries,
            "total": total,
            "truncated": total > _QUEUE_MAX_ENTRIES,
        }
    finally:
        conn.close()


def process_queue(
    settings: SubtoolsSettings | None = None,
    merge_fn: Any = None,
) -> dict[str, int]:
    """Process all pending queue entries.


    Called by the background worker. Attempts to merge for each
    pending entry where all languages are now present.


    Retry logic: on merge failure, entries are re-queued with exponential
    backoff (2^attempt_count minutes, capped at 60 minutes). After 10
    failed attempts the entry is permanently marked as 'failed'.


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
            created, _w = merge_fn(video_path, sub_paths, settings)
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
                # Exponential backoff: wait 2^attempt_count minutes before retrying
                # (capped at 60 minutes). Skip re-enqueue if not enough time has passed.
                backoff_minutes = min(2**entry.attempt_count, 60)
                try:
                    last_checked = datetime.fromisoformat(entry.last_checked)
                    elapsed_minutes = (
                        datetime.now(timezone.utc) - last_checked
                    ).total_seconds() / 60
                    if elapsed_minutes < backoff_minutes:
                        still_pending += 1
                        continue
                except (ValueError, TypeError):
                    pass
                enqueue(video_path, settings)
                still_pending += 1

    return {
        "checked": checked,
        "merged": merged,
        "failed": failed,
        "still_pending": still_pending,
    }


def _normalize_timestamp(ts: str | None) -> str | None:
    """Convert SQLite datetime to strict ISO 8601 for browser compat.

    SQLite stores timestamps as ``2026-06-06 08:00:00`` (space, no TZ).
    Safari / Firefox reject ``new Date()`` with that format; converting the
    space to ``T`` and appending ``Z`` produces a valid UTC timestamp that
    all browsers understand.
    """
    if ts is None or ts == "":
        return None
    # Already ISO?  (contains 'T' and ends with 'Z' or has offset)
    if "T" in ts and (ts.endswith("Z") or "+" in ts[10:] or ts.count("-") > 2):
        return ts
    # SQLite format: "YYYY-MM-DD HH:MM:SS"
    return ts.replace(" ", "T") + "Z"


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
                "created_at": _normalize_timestamp(row["first_seen"]),
                "updated_at": _normalize_timestamp(row["last_checked"]),
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


def get_history_entries_by_ids(
    ids: list[int],
    settings: SubtoolsSettings | None = None,
) -> list[dict]:
    """Load history entries with given IDs that have status 'done'.

    Uses parameterized queries (SQLite ``?`` placeholders) — no user-provided
    values are interpolated into the SQL string, preventing injection.

    Returns a list of dicts with 'output_files' parsed from JSON strings.

    Args:
        ids: List of entry IDs to fetch.
        settings: Configuration for DB path.

    Returns:
        List of dicts with 'id', 'video_path', 'output_files' (parsed as list[str]).
    """
    if not ids:
        return []
    conn = _get_connection(settings=settings)
    if conn is None:
        return []
    try:
        placeholders = ",".join("?" for _ in ids)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""SELECT id, video_path, output_files
                FROM pending_merges
                WHERE id IN ({placeholders}) AND status = 'done'""",
            ids,
        ).fetchall()
        return [
            {
                "id": row["id"],
                "video_path": row["video_path"],
                "output_files": json.loads(row["output_files"]) if row["output_files"] else [],
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_stats(settings: SubtoolsSettings | None = None) -> dict[str, Any]:
    """Compute aggregate statistics from the queue database.

    Returns a dict with:
        total_merged, total_failed, total_pending, success_rate,
        avg_retries, oldest_pending_hours, generated_at.
    """
    result: dict[str, Any] = {
        "total_merged": 0,
        "total_failed": 0,
        "total_pending": 0,
        "success_rate": 0.0,
        "avg_retries": 0.0,
        "oldest_pending_hours": None,
    }
    try:
        conn = _get_connection(settings=settings)
        if conn is None:
            return result
        try:
            row = conn.execute(
                """SELECT
                    COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END), 0) AS m,
                    COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0) AS f,
                    COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END), 0) AS p,
                    COALESCE(AVG(CASE WHEN status IN ('done','failed')
                        THEN attempt_count END), 0.0) AS avg_r
                FROM pending_merges"""
            ).fetchone()

            total_merged = row[0]
            total_failed = row[1]
            total_pending = row[2]
            avg_retries = float(row[3])

            total_done = total_merged + total_failed
            success_rate = total_merged / total_done if total_done > 0 else 0.0

            oldest_hours: float | None = None
            if total_pending > 0:
                oldest_row = conn.execute(
                    "SELECT MIN(first_seen) FROM pending_merges WHERE status='pending'"
                ).fetchone()
                if oldest_row and oldest_row[0]:
                    try:
                        first_seen = datetime.fromisoformat(oldest_row[0])
                        # Robustness: if fromisoformat returns naive datetime
                        # (Python 3.10 on timezone-naive stored strings), set UTC.
                        if first_seen.tzinfo is None:
                            first_seen = first_seen.replace(tzinfo=timezone.utc)
                        oldest_hours = round(
                            (datetime.now(timezone.utc) - first_seen).total_seconds() / 3600, 1
                        )
                    except (ValueError, TypeError):
                        pass

            result.update(
                {
                    "total_merged": total_merged,
                    "total_failed": total_failed,
                    "total_pending": total_pending,
                    "success_rate": round(success_rate, 4),
                    "avg_retries": round(avg_retries, 2),
                    "oldest_pending_hours": oldest_hours,
                }
            )
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to compute queue stats: {e}")

    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    return result


# Backward-compat re-exports from worker.py — remove after one release cycle
# (direct imports from submerge.worker are preferred)
from .worker import start_queue_worker, stop_queue_worker  # noqa: E402, F401
