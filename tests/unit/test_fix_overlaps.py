"""Tests for fix_single_track_overlaps and /api/fix-overlaps endpoint."""

from __future__ import annotations

from pathlib import Path

import pysubs2
import pytest
from starlette.testclient import TestClient

from submerge.merge import fix_single_track_overlaps


class TestFixSingleTrackOverlaps:
    """Unit tests for fix_single_track_overlaps in merge.py."""

    def test_no_overlaps(self, tmp_path: Path):
        """Idempotency — no event is modified, repositioned == 0."""
        srt = tmp_path / "no_overlap.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\nFirst\n\n"
            "2\n00:00:05,000 --> 00:00:07,000\nSecond\n\n"
            "3\n00:00:10,000 --> 00:00:12,000\nThird\n"
        )
        subs = pysubs2.load(str(srt))
        original_texts = [e.text for e in subs.events]

        fixed, count = fix_single_track_overlaps(subs)

        assert count == 0
        assert [e.text for e in fixed.events] == original_texts

    def test_two_simultaneous(self, tmp_path: Path):
        """Two overlapping events — later receives {\\an8}, repositioned == 1."""
        srt = tmp_path / "two_overlap.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\nFirst\n\n"
            "2\n00:00:03,000 --> 00:00:07,000\nSecond\n"
        )
        subs = pysubs2.load(str(srt))

        fixed, count = fix_single_track_overlaps(subs)

        assert count == 1
        events = fixed.events
        assert not events[0].text.startswith(r"{\an8}")  # First event unchanged
        assert events[1].text.startswith(r"{\an8}")  # Second event repositioned

    def test_three_simultaneous(self, tmp_path: Path):
        """Three simultaneous events — events 2+3 receive {\\an8}, repositioned == 2."""
        srt = tmp_path / "three_overlap.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:10,000\nFirst\n\n"
            "2\n00:00:02,000 --> 00:00:06,000\nSecond\n\n"
            "3\n00:00:04,000 --> 00:00:08,000\nThird\n"
        )
        subs = pysubs2.load(str(srt))

        fixed, count = fix_single_track_overlaps(subs)

        assert count == 2
        events = fixed.events
        assert not events[0].text.startswith(r"{\an8}")
        assert events[1].text.startswith(r"{\an8}")
        assert events[2].text.startswith(r"{\an8}")

    def test_corrupt_events_untouched(self, tmp_path: Path):
        """Events with end <= start remain unchanged."""
        srt = tmp_path / "corrupt.srt"
        srt.write_text(
            "1\n00:00:02,000 --> 00:00:01,000\nCorrupt\n\n"
            "2\n00:00:03,000 --> 00:00:05,000\nValid\n"
        )
        subs = pysubs2.load(str(srt))

        fixed, count = fix_single_track_overlaps(subs)

        assert count == 0
        events = fixed.events
        # Corrupt event is kept, valid event is kept, neither gets {\an8}
        assert not any(e.text.startswith(r"{\an8}") for e in events)

    def test_already_tagged_not_double_tagged(self, tmp_path: Path):
        """Events already tagged with {\\an8} are not double-tagged."""
        srt = tmp_path / "already_tagged.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\nFirst\n\n"
            "2\n00:00:03,000 --> 00:00:07,000\n{\\an8}Second\n"
        )
        subs = pysubs2.load(str(srt))

        fixed, count = fix_single_track_overlaps(subs)

        assert count == 0  # Already tagged, no new repositioning
        assert fixed.events[1].text == r"{\an8}Second"


class TestApiFixOverlapsEndpoint:
    """Integration tests for POST /api/fix-overlaps."""

    @pytest.fixture
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Create a TestClient with media root set to tmp_path."""
        import submerge.api as api
        import submerge.config as cfg

        monkeypatch.setenv("SUBTOOLS_PAIRS", "de-ko")
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        cfg.get_settings.cache_clear()
        api._runtime_settings.clear()

        return TestClient(api.app), tmp_path

    def test_valid_path(self, client):
        """Valid subtitle path with overlaps returns ok."""
        test_client, tmp_path = client

        ass = tmp_path / "test.ass"
        ass.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\nFirst\n\n"
            "2\n00:00:03,000 --> 00:00:07,000\nSecond\n"
        )

        resp = test_client.post(
            "/api/fix-overlaps",
            json={"subtitle_path": str(ass)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["repositioned"] == 1
        assert data["output_path"] == str(ass)

        # Verify file was modified — {\an8} preserved in ASS format
        subs = pysubs2.load(str(ass))
        assert subs.events[1].text.startswith(r"{\an8}")

    def test_missing_subtitle_path(self, client):
        """Empty subtitle_path returns 400."""
        test_client, _ = client

        resp = test_client.post(
            "/api/fix-overlaps",
            json={"subtitle_path": ""},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["status"] == "error"

    def test_no_subtitle_path_key(self, client):
        """Missing subtitle_path key returns 400."""
        test_client, _ = client

        resp = test_client.post(
            "/api/fix-overlaps",
            json={},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["status"] == "error"

    def test_nonexistent_file(self, client):
        """Nonexistent file returns 500."""
        test_client, tmp_path = client

        resp = test_client.post(
            "/api/fix-overlaps",
            json={"subtitle_path": str(tmp_path / "nonexistent.srt")},
        )
        assert resp.status_code == 500

    def test_no_overlaps_no_file_modification(self, client):
        """File with no overlaps is not overwritten (repositioned == 0)."""
        test_client, tmp_path = client

        srt = tmp_path / "clean.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\nFirst\n\n"
            "2\n00:00:05,000 --> 00:00:07,000\nSecond\n"
        )
        original_mtime = srt.stat().st_mtime

        resp = test_client.post(
            "/api/fix-overlaps",
            json={"subtitle_path": str(srt)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["repositioned"] == 0
        # File should not be overwritten when count == 0
        assert srt.stat().st_mtime == original_mtime
