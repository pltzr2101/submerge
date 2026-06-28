"""Tests for repair.py — single-track subtitle overlap fixing."""

from __future__ import annotations

import pytest
from pysubs2 import SSAEvent, SSAFile

from submerge.repair import (
    fix_overlaps_in_file,
    fix_single_track_overlaps,
    repair_all_subtitles_in_root,
    repair_subtitle_paths,
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

    def test_merged_output_srt_can_be_explicitly_called(self, tmp_path):
        """fix_overlaps_in_file works on merge-output files — the filter
        only applies in repair_all_subtitles_in_root, not here."""
        sub_file = tmp_path / "Movie.de-ko.srt"
        subs = _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt")
        subs.save(str(sub_file))
        result = fix_overlaps_in_file(sub_file)
        assert result["modified"] is True
        assert result["repositioned"] >= 1


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

    def test_merged_output_pattern_skipped(self, tmp_path):
        """Merge-output files like Movie.de-ko.srt are skipped by default."""
        overlapped = _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt")
        files = ["Movie.de-ko.srt", "Movie.en-de.srt", "Episode.S01E01.ja-de.srt"]
        mtimes_before = {}
        for name in files:
            p = tmp_path / name
            overlapped.save(str(p))
            mtimes_before[name] = p.stat().st_mtime

        result = repair_all_subtitles_in_root(tmp_path)
        assert result["total"] == 3
        assert result["skipped"] == 3
        assert result["fixed"] == 0

        # Files must remain untouched on disk (mtime unchanged)
        for name in files:
            assert (tmp_path / name).stat().st_mtime == mtimes_before[name]

    def test_custom_exclude_patterns(self, tmp_path):
        """Custom exclude_patterns override the default MERGED_OUTPUT_PATTERNS."""
        overlapped = _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt")

        # This file should be skipped by the custom pattern
        overlapped.save(str(tmp_path / "test.custom.srt"))
        # This file should be repaired
        overlapped.save(str(tmp_path / "normal.srt"))

        result = repair_all_subtitles_in_root(
            tmp_path,
            exclude_patterns=[r"\.custom\.(srt)$"],
        )
        assert result["skipped"] == 1
        assert result["fixed"] == 1
        assert result["total"] == 2

    def test_no_write_when_no_overlaps_preserves_mtime(self, tmp_path):
        """Clean .srt files are not written to disk — mtime stays unchanged."""
        clean = _make_subs([(0, 1000, "Line 1"), (2000, 3000, "Line 2")], fmt="srt")
        sub_file = tmp_path / "clean.srt"
        clean.save(str(sub_file))
        mtime_before = sub_file.stat().st_mtime

        result = repair_all_subtitles_in_root(tmp_path)
        assert result["total"] == 1
        assert result["fixed"] == 0
        assert sub_file.stat().st_mtime == mtime_before


class TestRepairSubtitlePaths:
    def test_happy_path_repairs_overlapping_files(self, tmp_path):
        """Explicit path list: overlapping files are repaired, clean ones are untouched."""
        bad1 = tmp_path / "bad1.srt"
        _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt").save(str(bad1))
        bad2 = tmp_path / "bad2.srt"
        _make_subs([(0, 3000, "X"), (1000, 4000, "Y")], fmt="srt").save(str(bad2))
        clean = tmp_path / "clean.srt"
        _make_subs([(0, 1000, "C"), (2000, 3000, "D")], fmt="srt").save(str(clean))

        result = repair_subtitle_paths([bad1, bad2, clean])
        assert result["total"] == 3
        assert result["fixed"] == 2
        assert result["skipped"] == 0
        assert result["failed"] == 0
        assert result["repositioned"] == 2

    def test_merged_output_skipped_by_default(self, tmp_path):
        """Merge-output filenames are skipped with default exclude_patterns."""
        merged = tmp_path / "Movie.de-ko.srt"
        _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt").save(str(merged))
        normal = tmp_path / "normal.srt"
        _make_subs([(0, 2000, "X"), (500, 2500, "Y")], fmt="srt").save(str(normal))

        result = repair_subtitle_paths([merged, normal])
        assert result["total"] == 2
        assert result["fixed"] == 1
        assert result["skipped"] == 1

    def test_custom_exclude_patterns(self, tmp_path):
        """Custom exclude_patterns override the default."""
        custom = tmp_path / "test.custom.srt"
        _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt").save(str(custom))
        normal = tmp_path / "normal.srt"
        _make_subs([(0, 2000, "X"), (500, 2500, "Y")], fmt="srt").save(str(normal))

        result = repair_subtitle_paths(
            [custom, normal],
            exclude_patterns=[r"\.custom\.(srt)$"],
        )
        assert result["skipped"] == 1
        assert result["fixed"] == 1

    def test_nonexistent_file_marks_failed(self, tmp_path):
        """Non-existent files are counted as failed, not fatal."""
        exists = tmp_path / "exists.srt"
        _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt").save(str(exists))

        result = repair_subtitle_paths([exists, tmp_path / "nope.srt"])
        assert result["total"] == 2
        assert result["fixed"] == 1
        assert result["failed"] == 1
        assert result["repositioned"] == 1

    def test_empty_paths_list(self, tmp_path):
        """Empty list returns all zeros."""
        result = repair_subtitle_paths([])
        assert result == {
            "total": 0,
            "fixed": 0,
            "skipped": 0,
            "failed": 0,
            "repositioned": 0,
        }

    def test_non_srt_paths_ignored(self, tmp_path):
        """Non-.srt files are silently skipped (not counted in total)."""
        ass_file = tmp_path / "test.ass"
        _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="ass").save(str(ass_file))
        srt_file = tmp_path / "test.srt"
        _make_subs([(0, 2000, "X"), (500, 2500, "Y")], fmt="srt").save(str(srt_file))

        result = repair_subtitle_paths([ass_file, srt_file])
        assert result["total"] == 1
        assert result["fixed"] == 1

    def test_no_write_when_clean_preserves_mtime(self, tmp_path):
        """Clean files are not rewritten — mtime unchanged."""
        sub_file = tmp_path / "clean.srt"
        _make_subs([(0, 1000, "Line 1"), (2000, 3000, "Line 2")], fmt="srt").save(str(sub_file))
        mtime_before = sub_file.stat().st_mtime

        result = repair_subtitle_paths([sub_file])
        assert result["fixed"] == 0
        assert result["total"] == 1
        assert sub_file.stat().st_mtime == mtime_before

    def test_deduplicates_paths(self, tmp_path):
        """Same path twice → total=1, not 2."""
        sub_file = tmp_path / "dedup.srt"
        _make_subs([(0, 2000, "A"), (500, 2500, "B")], fmt="srt").save(str(sub_file))

        result = repair_subtitle_paths([sub_file, sub_file])
        assert result["total"] == 1
        assert result["fixed"] == 1


class TestRepairAllFailedKey:
    def test_repair_all_returns_failed_key(self, tmp_path):
        """repair_all_subtitles_in_root returns 'failed' key for unparseable files."""
        # A binary file that cannot be parsed as subtitle
        (tmp_path / "bad.srt").write_bytes(b"\x00\x01\x02")

        result = repair_all_subtitles_in_root(tmp_path)
        assert result["total"] == 1
        assert result["failed"] == 1
        assert "failed" in result
