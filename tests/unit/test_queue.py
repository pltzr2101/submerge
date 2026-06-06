"""Tests for the SQLite retry queue."""

from __future__ import annotations

from pathlib import Path

import pytest

from submerge.config import get_settings_for_test
from submerge.queue import (
    _normalize_timestamp,
    dequeue,
    enqueue,
    get_all_entries,
    get_pending_entries,
    init_db,
    process_queue,
    record_failed,
    remove_entry,
)


@pytest.fixture
def queue_settings(tmp_path):
    """Settings with tmp_path as media_root and config_dir."""
    (tmp_path / "media").mkdir(exist_ok=True)
    (tmp_path / "config").mkdir(exist_ok=True)
    s = get_settings_for_test(
        pairs="de-ko",
        media_root=str(tmp_path / "media"),
        config_dir=str(tmp_path / "config"),
    )
    init_db(s)
    return s


class TestEnqueueDequeue:
    """Tests for enqueue and dequeue operations."""

    def test_enqueue_creates_entry(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "Show.mkv"
        video.touch()
        (tmp_path / "media" / "Show.de.srt").touch()

        enqueue(video, queue_settings)
        entries = get_all_entries(settings=queue_settings)["entries"]
        assert len(entries) == 1
        assert entries[0]["video_name"] == "Show.mkv"
        assert entries[0]["status"] == "pending"

    def test_enqueue_is_idempotent(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "Show.mkv"
        video.touch()
        (tmp_path / "media" / "Show.de.srt").touch()

        assert enqueue(video, queue_settings) is True  # New
        assert enqueue(video, queue_settings) is False  # Updated

        entries = get_all_entries(settings=queue_settings)["entries"]
        assert len(entries) == 1

    def test_dequeue_marks_done(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "Show.mkv"
        video.touch()

        enqueue(video, queue_settings)
        dequeue(video, "done", settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)["entries"]
        assert entries[0]["status"] == "done"

    def test_dequeue_marks_failed(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "Show.mkv"
        video.touch()

        enqueue(video, queue_settings)
        dequeue(video, "failed", "Test error", settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)["entries"]
        assert entries[0]["status"] == "failed"
        assert entries[0]["error_msg"] == "Test error"

    def test_remove_entry(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "Show.mkv"
        video.touch()

        enqueue(video, queue_settings)
        remove_entry(video, settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)["entries"]
        assert len(entries) == 0

    def test_tracks_present_and_missing(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "Show.mkv"
        video.touch()
        (tmp_path / "media" / "Show.de.srt").touch()
        # No ko.srt

        enqueue(video, queue_settings)
        entries = get_all_entries(settings=queue_settings)["entries"]
        assert "de" in entries[0]["langs_present"]
        assert "ko" in entries[0]["langs_missing"]


class TestProcessQueue:
    """Tests for queue processing."""

    def test_merges_when_all_langs_present(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "Show.mkv"
        video.touch()
        (tmp_path / "media" / "Show.de.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        (tmp_path / "media" / "Show.ko.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n테스트\n"
        )

        enqueue(video, queue_settings)
        result = process_queue(queue_settings)

        assert result["merged"] == 1
        assert (tmp_path / "media" / "Show.de-ko.ass").exists()

        # Entry should be marked done
        entries = get_all_entries(settings=queue_settings)["entries"]
        assert entries[0]["status"] == "done"

    def test_keeps_pending_when_langs_missing(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "Show.mkv"
        video.touch()
        (tmp_path / "media" / "Show.de.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")

        enqueue(video, queue_settings)
        result = process_queue(queue_settings)

        assert result["merged"] == 0
        assert result["still_pending"] >= 1
        entries = get_all_entries(settings=queue_settings)["entries"]
        assert entries[0]["status"] == "pending"

    def test_fails_on_missing_video(self, tmp_path, queue_settings):
        video = tmp_path / "media" / "nonexistent.mkv"

        enqueue(video, queue_settings)  # We can enqueue a path that doesn't exist yet
        result = process_queue(queue_settings)

        assert result["failed"] >= 1
        entries = get_all_entries(settings=queue_settings)["entries"]
        assert entries[0]["status"] == "failed"


class TestGetPendingEntries:
    """Tests for filtering pending entries."""

    def test_returns_only_pending(self, tmp_path, queue_settings):
        v1 = tmp_path / "media" / "Show1.mkv"
        v2 = tmp_path / "media" / "Show2.mkv"
        v1.touch()
        v2.touch()

        enqueue(v1, queue_settings)
        enqueue(v2, queue_settings)
        dequeue(v2, "done", settings=queue_settings)

        pending = get_pending_entries(settings=queue_settings)
        assert len(pending) == 1
        assert Path(pending[0].video_path).name == "Show1.mkv"

    def test_returns_empty_when_none_pending(self, tmp_path, queue_settings):
        pending = get_pending_entries(settings=queue_settings)
        assert len(pending) == 0


class TestRecordFailed:
    """Tests for atomic failed-entry recording."""

    def test_creates_failed_entry_directly(self, tmp_path, queue_settings):
        """record_failed inserts 'failed' status without ever being 'pending'."""
        video = tmp_path / "media" / "Show.mkv"
        video.touch()

        record_failed(video, "something broke", settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)["entries"]
        assert len(entries) == 1
        assert entries[0]["status"] == "failed"
        assert entries[0]["error_msg"] == "something broke"
        # It should never show up in pending entries
        pending = get_pending_entries(settings=queue_settings)
        assert len(pending) == 0

    def test_update_existing_to_failed(self, tmp_path, queue_settings):
        """record_failed on an existing pending entry updates it to failed."""
        video = tmp_path / "media" / "Show.mkv"
        video.touch()

        enqueue(video, queue_settings)
        record_failed(video, "merge crashed", settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)["entries"]
        assert len(entries) == 1
        assert entries[0]["status"] == "failed"
        assert entries[0]["error_msg"] == "merge crashed"
        pending = get_pending_entries(settings=queue_settings)
        assert len(pending) == 0

    def test_record_failed_no_video_file_needed(self, tmp_path, queue_settings):
        """record_failed works even if the video file doesn't exist."""
        video = tmp_path / "media" / "gone.mkv"
        # Don't create the file

        record_failed(video, "file missing", settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)["entries"]
        assert len(entries) == 1
        assert entries[0]["status"] == "failed"
        assert entries[0]["video_name"] == "gone.mkv"


class TestGetAllEntriesTruncation:
    """Tests for get_all_entries truncation info."""

    def test_no_truncation_when_few_entries(self, tmp_path, queue_settings):
        """truncated=False and total matches count when under limit."""
        video = tmp_path / "media" / "Show.mkv"
        video.touch()

        enqueue(video, queue_settings)
        result = get_all_entries(settings=queue_settings)
        assert result["truncated"] is False
        assert result["total"] == 1
        assert len(result["entries"]) == 1

    def test_truncation_total_accurate(self, tmp_path, queue_settings):
        """total reflects true count even when entries are truncated."""
        # Create multiple entries
        for i in range(3):
            v = tmp_path / "media" / f"Show{i}.mkv"
            v.touch()
            enqueue(v, queue_settings)

        result = get_all_entries(settings=queue_settings)
        assert result["total"] == 3
        assert result["truncated"] is False
        assert len(result["entries"]) == 3

    def test_empty_returns_zero_total(self, tmp_path, queue_settings):
        """Empty queue returns total=0, truncated=False."""
        result = get_all_entries(settings=queue_settings)
        assert result["total"] == 0
        assert result["truncated"] is False
        assert result["entries"] == []


class TestProcessQueueBackoff:
    """Tests for exponential backoff on queue merge failure."""

    def test_backoff_skips_recent_retries(self, tmp_path, queue_settings):
        """After a merge failure, immediate re-enqueue is skipped due to backoff."""
        video = tmp_path / "media" / "Show.mkv"
        video.touch()
        (tmp_path / "media" / "Show.de.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        (tmp_path / "media" / "Show.ko.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n테스트\n"
        )

        enqueue(video, queue_settings)

        # Simulate a merge that always raises
        def _failing_merge(*args, **kwargs):
            raise RuntimeError("simulated failure")

        # 1st attempt — should fail and stay pending (backoff 1 min)
        result = process_queue(queue_settings, merge_fn=_failing_merge)
        assert result["still_pending"] == 1
        assert result["failed"] == 0

        all_entries = get_all_entries(settings=queue_settings)["entries"]
        assert all_entries[0]["status"] == "pending"
        # attempt_count stays unchanged because backoff skipped the re-enqueue
        assert all_entries[0]["attempt_count"] == 0


# ---------------------------------------------------------------------------
# Timestamp normalization (SQLite → ISO 8601 for browser compat)
# ---------------------------------------------------------------------------


class TestNormalizeTimestamp:
    """Tests for _normalize_timestamp()."""

    def test_sqlite_format_converted(self):
        """SQLite 'YYYY-MM-DD HH:MM:SS' → ISO 8601 with T and Z."""
        assert _normalize_timestamp("2026-06-06 08:00:00") == "2026-06-06T08:00:00Z"

    def test_already_iso_untouched(self):
        """Already valid ISO 8601 is returned unchanged."""
        assert _normalize_timestamp("2026-06-06T08:00:00Z") == "2026-06-06T08:00:00Z"

    def test_already_iso_with_offset_untouched(self):
        """ISO with +HH:MM offset is returned unchanged."""
        assert _normalize_timestamp("2026-06-06T08:00:00+02:00") == "2026-06-06T08:00:00+02:00"

    def test_none_returns_none(self):
        """None → None."""
        assert _normalize_timestamp(None) is None

    def test_empty_string_returns_none(self):
        """Empty string → None."""
        assert _normalize_timestamp("") is None
