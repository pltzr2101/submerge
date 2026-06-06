"""Tests for merge quality checks."""

from __future__ import annotations

import pytest

from submerge.merge import (
    InvalidSubtitleError,
    MergeConfig,
    QualityWarning,
    deduplicate_near_dupes,
    merge_bilingual,
    run_quality_checks,
)


def _make_ass_events(events_data):
    """Create an SSAFile with given events.

    events_data: list of (start_ms, end_ms, style) tuples.
    """
    from pysubs2 import SSAEvent, SSAFile

    subs = SSAFile()
    for start, end, style in events_data:
        e = SSAEvent()
        e.start = start
        e.end = end
        e.style = style
        e.text = f"Text {style} {start}-{end}"
        subs.append(e)
    return subs


class TestQualityWarning:
    """Tests for QualityWarning dataclass."""

    def test_dataclass_creation(self):
        """QualityWarning can be created with all fields."""
        w = QualityWarning(code="TEST", message="test message", severity="warning")
        assert w.code == "TEST"
        assert w.message == "test message"
        assert w.severity == "warning"


class TestRunQualityChecksHappyPath:
    """Happy path — no warnings for balanced events."""

    def test_no_warnings_for_balanced_events(self):
        """Balanced bottom/top events produce no warnings."""
        subs = _make_ass_events(
            [
                (1000, 3000, "bottom"),
                (5000, 7000, "bottom"),
                (1000, 3000, "top"),
                (5000, 7000, "top"),
            ]
        )
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        assert warnings == []


class TestOverlapBottom:
    """Check A: OVERLAP_BOTTOM."""

    def test_overlap_warning_with_5_overlaps(self):
        """5 overlapping bottom events produce warning."""
        subs = _make_ass_events(
            [
                (1000, 3000, "bottom"),  # overlap with next
                (2000, 4000, "bottom"),  # overlap with next
                (3000, 5000, "bottom"),  # overlap with next
                (4000, 6000, "bottom"),  # overlap with next
                (5000, 7000, "bottom"),  # <- 5 overlaps total
                (0, 500, "top"),
            ]
        )
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        assert len(warnings) >= 1
        overlap_w = next(w for w in warnings if w.code == "OVERLAP_BOTTOM")
        assert overlap_w.severity == "warning"
        assert "overlapping" in overlap_w.message.lower()

    def test_no_overlap_warning_with_2_overlaps(self):
        """2 overlapping bottom events do NOT produce warning (< 3 threshold)."""
        subs = _make_ass_events(
            [
                (1000, 3000, "bottom"),
                (2000, 3000, "bottom"),  # overlap 1
                (3500, 5000, "bottom"),
                (4500, 6000, "bottom"),  # overlap 2
                (0, 500, "top"),
            ]
        )
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        overlap_warnings = [w for w in warnings if w.code == "OVERLAP_BOTTOM"]
        assert len(overlap_warnings) == 0


class TestSuspiciousRatio:
    """Check B: SUSPICIOUS_RATIO."""

    def test_ratio_warning_30_bottom_3_top(self):
        """30 bottom + 3 top events produce warning."""
        events = []
        for i in range(30):
            events.append((i * 100, i * 100 + 50, "bottom"))
        for i in range(3):
            events.append((i * 100, i * 100 + 50, "top"))
        subs = _make_ass_events(events)
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        ratio_w = next(w for w in warnings if w.code == "SUSPICIOUS_RATIO")
        assert ratio_w.severity == "warning"
        assert "imbalance" in ratio_w.message.lower()


class TestLowCoverage:
    """Check C: LOW_COVERAGE."""

    def test_low_coverage_warning(self):
        """10 bottom events outside all top time ranges produce warning."""
        events = []
        # All bottom events at 0-1000
        for i in range(10):
            events.append((i * 10, i * 10 + 5, "bottom"))
        # Top event entirely outside (at 10000)
        events.append((10000, 11000, "top"))
        subs = _make_ass_events(events)
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        coverage_w = next(w for w in warnings if w.code == "LOW_COVERAGE")
        assert coverage_w.severity == "warning"
        assert "0%" in coverage_w.message


class TestEmptyTrack:
    """Check D: EMPTY_TRACK."""

    def test_empty_top_track(self):
        """Empty top track produces error severity."""
        subs = _make_ass_events(
            [
                (1000, 2000, "bottom"),
            ]
        )
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        empty_w = next(w for w in warnings if w.code == "EMPTY_TRACK")
        assert empty_w.severity == "error"
        assert "sub2.srt" in empty_w.message

    def test_empty_bottom_track(self):
        """Empty bottom track produces error severity with sub1_name."""
        subs = _make_ass_events(
            [
                (1000, 2000, "top"),
            ]
        )
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        empty_w = next(w for w in warnings if w.code == "EMPTY_TRACK")
        assert empty_w.severity == "error"
        assert "sub1.srt" in empty_w.message

    def test_both_tracks_empty(self):
        """Both tracks empty produce two independent EMPTY_TRACK warnings."""
        subs = _make_ass_events([])
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        empty_warnings = [w for w in warnings if w.code == "EMPTY_TRACK"]
        assert len(empty_warnings) == 2
        codes = {w.message for w in empty_warnings}
        assert "Track 'sub1.srt' contributed 0 events to merged output" in codes
        assert "Track 'sub2.srt' contributed 0 events to merged output" in codes


class TestCombined:
    """Combined warnings."""

    def test_overlap_and_ratio_both(self):
        """Both OVERLAP and RATIO warnings can appear together."""
        events = []
        # 30 bottom — overlap with each other
        for i in range(30):
            events.append((i * 50, i * 50 + 60, "bottom"))  # overlapping
        # Only 3 top
        for i in range(3):
            events.append((i * 100, i * 100 + 50, "top"))
        subs = _make_ass_events(events)
        warnings = run_quality_checks(subs, "sub1.srt", "sub2.srt")
        codes = {w.code for w in warnings}
        assert "OVERLAP_BOTTOM" in codes
        assert "SUSPICIOUS_RATIO" in codes


class TestMergeBilingualIntegration:
    """Integration tests for merge_bilingual returning tuple with warnings."""

    def test_merge_bilingual_returns_tuple_with_warnings(
        self, tmp_path, sample_srt_fr, sample_srt_pl
    ):
        """merge_bilingual returns (Path, list[QualityWarning])."""
        output = tmp_path / "output.ass"
        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        result = merge_bilingual(sample_srt_fr, sample_srt_pl, output, config)
        assert isinstance(result, tuple)
        assert len(result) == 2
        out_path, warnings = result
        assert out_path == output
        assert output.exists()
        assert isinstance(warnings, list)
        # Balanced data: no warnings
        for w in warnings:
            assert w.code != "EMPTY_TRACK"

    def test_merge_bilingual_empty_file(self, tmp_path, sample_srt_pl):
        """merge_bilingual with empty bottom file raises error."""
        empty_srt = tmp_path / "empty.srt"
        empty_srt.write_text("")
        output = tmp_path / "output.ass"
        with pytest.raises(InvalidSubtitleError):
            merge_bilingual(empty_srt, sample_srt_pl, output)


# =============================================================================
# Near-duplicate bottom-event dedup
# =============================================================================


def _make_ev(start: int, end: int, style: str, text: str = ""):
    """Create a minimal SSAEvent for dedup testing."""
    import pysubs2  # noqa: F811

    ev = pysubs2.SSAEvent()
    ev.start = start
    ev.end = end
    ev.style = style
    ev.text = text
    ev.plaintext = text
    return ev


class TestDeduplicateNearDupes:
    """Tests for deduplicate_near_dupes()."""

    def test_overlap_and_similar_text_removes_one(self):
        """Two bottom events with 200ms+ overlap and ratio >= 0.85 → one removed."""
        events = [
            _make_ev(1000, 3000, "bottom", "Wo ist sie?"),
            _make_ev(1000, 2900, "bottom", "Wo ist sie?"),
            _make_ev(1000, 3000, "top", "Where is she?"),
        ]
        result = deduplicate_near_dupes(events)
        n_bottom = sum(1 for e in result if e.style == "bottom")
        assert n_bottom == 1

    def test_similar_but_not_identical_text_removes_one(self):
        """Near-identical text (ratio >= 0.85) + overlap → one removed."""
        events = [
            _make_ev(1000, 3000, "bottom", "Wo ist sie hin?"),
            _make_ev(1050, 3050, "bottom", "Wo ist sie hin"),
            _make_ev(1000, 3000, "top", "Where is she?"),
        ]
        result = deduplicate_near_dupes(events)
        n_bottom = sum(1 for e in result if e.style == "bottom")
        assert n_bottom == 1

    def test_identical_text_no_overlap_keeps_both(self):
        """Identical text but < 200ms overlap → both kept."""
        events = [
            _make_ev(1000, 2000, "bottom", "Wo ist sie?"),
            _make_ev(2100, 3000, "bottom", "Wo ist sie?"),  # overlap = 0
            _make_ev(1000, 3000, "top", "Where is she?"),
        ]
        result = deduplicate_near_dupes(events)
        n_bottom = sum(1 for e in result if e.style == "bottom")
        assert n_bottom == 2

    def test_completely_different_text_keeps_both(self):
        """Large overlap but completely different text → both kept."""
        events = [
            _make_ev(1000, 3000, "bottom", "Wo ist sie?"),
            _make_ev(1200, 3200, "bottom", "Was machen wir heute?"),
            _make_ev(1000, 3000, "top", "Where is she?"),
        ]
        result = deduplicate_near_dupes(events)
        n_bottom = sum(1 for e in result if e.style == "bottom")
        assert n_bottom == 2

    def test_top_events_untouched(self):
        """Top events are never candidates for removal."""
        events = [
            _make_ev(1000, 3000, "top", "Line 1"),
            _make_ev(2000, 4000, "top", "Line 2"),
        ]
        result = deduplicate_near_dupes(events)
        assert len(result) == 2

    def test_winner_has_better_top_overlap(self):
        """Tiebreaker: event with larger top overlap survives."""
        events = [
            _make_ev(1000, 3000, "bottom", "same text"),  # 2000ms top overlap
            _make_ev(2500, 3500, "bottom", "same text"),  # 500ms top overlap
            _make_ev(1000, 3000, "top", "Top line"),
        ]
        result = deduplicate_near_dupes(events)
        n_bottom = sum(1 for e in result if e.style == "bottom")
        assert n_bottom == 1
        kept = [e for e in result if e.style == "bottom"][0]
        # The earlier event (1000-3000) has larger top overlap (2000 > 500)
        assert kept.start == 1000
        assert kept.end == 3000
