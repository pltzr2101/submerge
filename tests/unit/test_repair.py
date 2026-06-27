"""Tests for repair.py — single-track subtitle overlap fixing."""

from __future__ import annotations

import pytest
from pysubs2 import SSAEvent, SSAFile

from submerge.repair import fix_overlaps_in_file, fix_single_track_overlaps


def _make_subs(events: list[tuple[int, int, str]], fmt: str = "ass") -> SSAFile:
    """Helper: build SSAFile from (start_ms, end_ms, text) tuples."""
    subs = SSAFile()
    subs.format = fmt
    for start, end, text in events:
        ev = SSAEvent(start=start, end=end, text=text)
        subs.events.append(ev)
    return subs


class TestFixSingleTrackOverlaps:
    def test_no_overlaps_is_idempotent(self):
        subs = _make_subs([(0, 1000, "Line 1"), (2000, 3000, "Line 2")])
        _, count = fix_single_track_overlaps(subs)
        assert count == 0

    def test_ass_two_simultaneous_events(self):
        subs = _make_subs([(0, 2000, "Speaker A"), (500, 2500, "Speaker B")], fmt="ass")
        fixed, count = fix_single_track_overlaps(subs)
        assert count == 1
        texts = [e.text for e in fixed.events]
        assert any(t.startswith(r"{\an8}") for t in texts)
        assert any(not t.startswith(r"{\an8}") for t in texts)

    def test_ass_idempotent_already_fixed(self):
        subs = _make_subs([(0, 2000, "Speaker A"), (500, 2500, "Speaker B")], fmt="ass")
        fixed1, _ = fix_single_track_overlaps(subs)
        _, count2 = fix_single_track_overlaps(fixed1)
        assert count2 == 0

    def test_srt_two_simultaneous_events(self):
        subs = _make_subs([(0, 2000, "Speaker A"), (500, 2500, "Speaker B")], fmt="srt")
        fixed, count = fix_single_track_overlaps(subs)
        assert count == 1
        events = sorted(fixed.events, key=lambda e: e.start)
        assert events[0].end <= events[1].start

    def test_srt_no_ass_tags_injected(self):
        subs = _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt")
        fixed, _ = fix_single_track_overlaps(subs)
        for ev in fixed.events:
            assert r"{\an8}" not in ev.text

    def test_three_simultaneous_events(self):
        subs = _make_subs(
            [
                (0, 3000, "A"),
                (500, 3500, "B"),
                (1000, 4000, "C"),
            ],
            fmt="ass",
        )
        _, count = fix_single_track_overlaps(subs)
        assert count >= 2

    def test_corrupt_events_untouched(self):
        subs = _make_subs([(0, 0, "corrupt"), (1000, 2000, "normal")])
        _, count = fix_single_track_overlaps(subs)
        assert count == 0


class TestFixOverlapsInFile:
    def test_file_written_when_overlaps_found(self, tmp_path):
        sub_file = tmp_path / "test.srt"
        subs = _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt")
        subs.save(str(sub_file))
        result = fix_overlaps_in_file(sub_file)
        assert result["modified"] is True
        assert result["repositioned"] >= 1
        assert result["output_path"] == str(sub_file)

    def test_file_not_written_when_clean(self, tmp_path):
        sub_file = tmp_path / "clean.srt"
        subs = _make_subs([(0, 1000, "A"), (2000, 3000, "B")], fmt="srt")
        subs.save(str(sub_file))
        mtime_before = sub_file.stat().st_mtime
        result = fix_overlaps_in_file(sub_file)
        assert result["modified"] is False
        assert sub_file.stat().st_mtime == mtime_before

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            fix_overlaps_in_file(tmp_path / "nonexistent.srt")
