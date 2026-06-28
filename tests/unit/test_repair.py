"""Tests for repair.py — single-track subtitle overlap fixing."""

from __future__ import annotations

import pytest
from pysubs2 import SSAEvent, SSAFile

from submerge.repair import (
    fix_overlaps_in_file,
    fix_single_track_overlaps,
    repair_all_subtitles_in_root,
)


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

    def test_srt_idempotent_already_fixed(self):
        subs = _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt")
        fixed1, count1 = fix_single_track_overlaps(subs)
        assert count1 == 1
        _, count2 = fix_single_track_overlaps(fixed1)
        assert count2 == 0

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


class TestRepairAllSubtitlesInRoot:
    def test_all_clean_no_modifications(self, tmp_path):
        """When every .srt is clean, fixed=0 and files are untouched."""
        for i in range(3):
            s = _make_subs([(0, 1000, f"Line {i}")], fmt="srt")
            s.save(str(tmp_path / f"clean_{i}.srt"))
        result = repair_all_subtitles_in_root(tmp_path)
        assert result["total"] == 3
        assert result["fixed"] == 0

    def test_some_overlapping_get_repaired(self, tmp_path):
        """Overlapping .srt files are repaired; clean ones are skipped."""
        clean = _make_subs([(0, 1000, "ok")], fmt="srt")
        clean.save(str(tmp_path / "clean.srt"))

        bad = _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt")
        bad.save(str(tmp_path / "bad.srt"))

        result = repair_all_subtitles_in_root(tmp_path)
        assert result["total"] == 2
        assert result["fixed"] == 1

    def test_non_srt_files_ignored(self, tmp_path):
        """Only .srt files are targeted; .ass and others are skipped."""
        s = _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="ass")
        s.save(str(tmp_path / "test.ass"))
        result = repair_all_subtitles_in_root(tmp_path)
        assert result["total"] == 0
        assert result["fixed"] == 0

    def test_unparseable_file_skipped(self, tmp_path):
        """A binary .srt file is skipped gracefully (total incremented, fixed not)."""
        (tmp_path / "binary.srt").write_bytes(b"\x00\x01\x02")
        result = repair_all_subtitles_in_root(tmp_path)
        assert result["total"] == 1
        assert result["fixed"] == 0
