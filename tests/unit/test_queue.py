"""Tests for the SQLite retry queue."""

from __future__ import annotations

from pathlib import Path

import pytest

from submerge.config import get_settings_for_test
from submerge.queue import (
    dequeue,
    enqueue,
    get_all_entries,
    get_pending_entries,
    init_db,
    process_queue,
    remove_entry,
)


@pytest.fixture
def queue_settings(tmp_path):
    """Settings with tmp_path as media_root."""
    s = get_settings_for_test(
        pairs="de-ko",
        media_root=str(tmp_path),
    )
    init_db(s)
    return s


class TestEnqueueDequeue:
    """Tests for enqueue and dequeue operations."""

    def test_enqueue_creates_entry(self, tmp_path, queue_settings):
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()

        enqueue(video, queue_settings)
        entries = get_all_entries(settings=queue_settings)
        assert len(entries) == 1
        assert entries[0]["video_name"] == "Show.mkv"
        assert entries[0]["status"] == "pending"

    def test_enqueue_is_idempotent(self, tmp_path, queue_settings):
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()

        assert enqueue(video, queue_settings) is True  # New
        assert enqueue(video, queue_settings) is False  # Updated

        entries = get_all_entries(settings=queue_settings)
        assert len(entries) == 1

    def test_dequeue_marks_done(self, tmp_path, queue_settings):
        video = tmp_path / "Show.mkv"
        video.touch()

        enqueue(video, queue_settings)
        dequeue(video, "done", settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)
        assert entries[0]["status"] == "done"

    def test_dequeue_marks_failed(self, tmp_path, queue_settings):
        video = tmp_path / "Show.mkv"
        video.touch()

        enqueue(video, queue_settings)
        dequeue(video, "failed", "Test error", settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)
        assert entries[0]["status"] == "failed"
        assert entries[0]["error_msg"] == "Test error"

    def test_remove_entry(self, tmp_path, queue_settings):
        video = tmp_path / "Show.mkv"
        video.touch()

        enqueue(video, queue_settings)
        remove_entry(video, settings=queue_settings)

        entries = get_all_entries(settings=queue_settings)
        assert len(entries) == 0

    def test_tracks_present_and_missing(self, tmp_path, queue_settings):
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()
        # No ko.srt

        enqueue(video, queue_settings)
        entries = get_all_entries(settings=queue_settings)
        assert "de" in entries[0]["langs_present"]
        assert "ko" in entries[0]["langs_missing"]


class TestProcessQueue:
    """Tests for queue processing."""

    def test_merges_when_all_langs_present(self, tmp_path, queue_settings):
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        (tmp_path / "Show.ko.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n테스트\n")

        enqueue(video, queue_settings)
        result = process_queue(queue_settings)

        assert result["merged"] == 1
        assert (tmp_path / "Show.de-ko.ass").exists()

        # Entry should be marked done
        entries = get_all_entries(settings=queue_settings)
        assert entries[0]["status"] == "done"

    def test_keeps_pending_when_langs_missing(self, tmp_path, queue_settings):
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")

        enqueue(video, queue_settings)
        result = process_queue(queue_settings)

        assert result["merged"] == 0
        assert result["still_pending"] >= 1
        entries = get_all_entries(settings=queue_settings)
        assert entries[0]["status"] == "pending"

    def test_fails_on_missing_video(self, tmp_path, queue_settings):
        video = tmp_path / "nonexistent.mkv"

        enqueue(video, queue_settings)  # We can enqueue a path that doesn't exist yet
        result = process_queue(queue_settings)

        assert result["failed"] >= 1
        entries = get_all_entries(settings=queue_settings)
        assert entries[0]["status"] == "failed"


class TestGetPendingEntries:
    """Tests for filtering pending entries."""

    def test_returns_only_pending(self, tmp_path, queue_settings):
        v1 = tmp_path / "Show1.mkv"
        v2 = tmp_path / "Show2.mkv"
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
