"""Tests for bottom-dedup by top-coverage (_deduplicate_bottom_by_top_coverage)."""

from __future__ import annotations

from pathlib import Path

import pysubs2

from submerge.merge import _deduplicate_bottom_by_top_coverage, merge_bilingual


def make_event(start: int, end: int, style: str, text: str = "x") -> pysubs2.SSAEvent:
    """Create a minimal SSAEvent for testing."""
    ev = pysubs2.SSAEvent()
    ev.start = start
    ev.end = end
    ev.style = style
    ev.text = text
    ev.plaintext = text
    return ev


# =============================================================================
# Unit tests for _deduplicate_bottom_by_top_coverage
# =============================================================================


class TestBottomDedupHappyPath:
    """Happy-path: no overlapping bottom events → nothing removed."""

    def test_no_overlap_between_bottoms_keeps_all(self):
        """Two sequential bottom events under one top → both kept."""
        events = [
            make_event(1000, 2000, "bottom", "A"),
            make_event(2000, 3000, "bottom", "B"),
            make_event(1000, 3000, "top", "T1"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 0
        assert len(result) == 3


class TestBottomDedupRealDuplicate:
    """True duplicate: mutual-overlapping bottoms under same top → one kept."""

    def test_two_overlapping_bottoms_removes_one(self):
        """1 top [1000-3000] + 2 bottoms [1000-3000] and [1050-3050]."""
        events = [
            make_event(1000, 3000, "bottom", "dup-A"),
            make_event(1050, 3050, "bottom", "dup-B"),
            make_event(1000, 3000, "top", "T1"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 1
        assert len(result) == 2

    def test_survivor_has_greatest_overlap_with_top(self):
        """Kept bottom is the one with the largest top-overlap."""
        events = [
            make_event(1000, 3000, "bottom", "best"),  # 2000 ms overlap
            make_event(2500, 3000, "bottom", "worst"),  # 500 ms overlap
            make_event(1000, 3000, "top", "T1"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 1
        kept_texts = {e.text for e in result if e.style == "bottom"}
        assert kept_texts == {"best"}


class TestBottomDedupLegitTwoLiner:
    """False-positive protection: sequential 2-liner not removed."""

    def test_two_liner_keeps_both(self):
        """Bottom events [1000-2000] and [2000-3000] have NO mutual overlap."""
        events = [
            make_event(1000, 2000, "bottom", "line1"),
            make_event(2000, 3000, "bottom", "line2"),
            make_event(1000, 4000, "top", "T1"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 0
        assert len(result) == 3

    def test_two_liner_non_touching_keeps_both(self):
        """Bottom events [1000-1800] and [2200-3000] have gap, no overlap."""
        events = [
            make_event(1000, 1800, "bottom", "line1"),
            make_event(2200, 3000, "bottom", "line2"),
            make_event(1000, 4000, "top", "T1"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 0


class TestBottomDedupThreeDups:
    """Three overlapping bottoms → keep one, remove two."""

    def test_three_overlapping_bottoms_removes_two(self):
        """All three bottom events overlap each other mutually."""
        events = [
            make_event(1000, 3000, "bottom", "A"),
            make_event(1100, 2900, "bottom", "B"),
            make_event(1200, 2800, "bottom", "C"),
            make_event(1000, 3000, "top", "T1"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 2
        assert len(result) == 2


class TestTopEventsUntouched:
    """Top events are never candidates for removal."""

    def test_overlapping_tops_not_removed(self):
        """Two top events overlap each other but are untouched."""
        events = [
            make_event(1000, 3000, "top", "T1"),
            make_event(2000, 4000, "top", "T2"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 0
        assert len(result) == 2


class TestOtherStyleEventsUntouched:
    """Non-top/bottom events never modified."""

    def test_default_style_not_removed(self):
        """Events with style='Default' pass through."""
        events = [
            make_event(1000, 3000, "Default", "D1"),
            make_event(1050, 3050, "Default", "D2"),
            make_event(2000, 4000, "top", "T1"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 0
        assert len(result) == 3


class TestOrphanBottoms:
    """Bottom events without any top → all kept."""

    def test_no_top_keeps_all_bottoms(self):
        """Orphan bottom events are never removed."""
        events = [
            make_event(1000, 3000, "bottom", "B1"),
            make_event(1050, 3050, "bottom", "B2"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 0
        assert len(result) == 2


class TestMixedCase:
    """Combination of duplicates and legitimate 2-liners."""

    def test_mixed_case_correct(self):
        """5 tops, 2 with dup-pairs, 1 with legit 2-liner → removed=2."""
        events = [
            # Top 1: legit 2-liner — keep both
            make_event(1000, 2000, "bottom", "legit-1"),
            make_event(2000, 3000, "bottom", "legit-2"),
            make_event(1000, 3000, "top", "T1"),
            # Top 2: true duplicate — remove one
            make_event(4000, 6000, "bottom", "dupA-1"),
            make_event(4100, 6100, "bottom", "dupA-2"),
            make_event(4000, 6000, "top", "T2"),
            # Top 3: true duplicate — remove one
            make_event(7000, 9000, "bottom", "dupB-1"),
            make_event(7200, 9200, "bottom", "dupB-2"),
            make_event(7000, 9000, "top", "T3"),
            # Top 4: single bottom — keep
            make_event(10000, 12000, "bottom", "single"),
            make_event(10000, 12000, "top", "T4"),
            # Top 5: single bottom — keep
            make_event(13000, 15000, "bottom", "single2"),
            make_event(13000, 15000, "top", "T5"),
        ]
        result, removed = _deduplicate_bottom_by_top_coverage(events)
        assert removed == 2
        # 5 top + 8 bottom - 2 removed = 11 events
        assert len(result) == 11
        # Legit 2-liner still intact
        bottom_texts = {e.text for e in result if e.style == "bottom"}
        assert "legit-1" in bottom_texts
        assert "legit-2" in bottom_texts


# =============================================================================
# Integration test via merge_bilingual
# =============================================================================


class TestBottomDedupIntegration:
    """End-to-end: merge_bilingual with two similar bottom-language sources."""

    def test_merge_bilingual_with_same_lang_dedup(self, tmp_path: Path):
        """Two same-language SRT inputs → fewer output events than sum."""
        sub1 = tmp_path / "de1.srt"
        sub2 = tmp_path / "de2.srt"
        output = tmp_path / "out.ass"

        # Two near-identical German subtitle files (same timing, different text)
        sub1.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\nWie geht es Ihnen?\n\n"
            "2\n00:00:05,000 --> 00:00:08,000\nMir geht es gut.\n\n"
            "3\n00:00:10,000 --> 00:00:14,000\nWas machen wir heute?\n"
        )
        sub2.write_text(
            "1\n00:00:01,000 --> 00:00:03,900\nWie geht's?\n\n"
            "2\n00:00:05,100 --> 00:00:08,000\nAlles gut, danke.\n\n"
            "3\n00:00:10,000 --> 00:00:14,000\nWas tun wir heute?\n"
        )

        # Korean top track
        ko_srt = tmp_path / "ko.srt"
        ko_srt.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\n어떻게 지내세요?\n\n"
            "2\n00:00:05,000 --> 00:00:08,000\n잘 지내요.\n\n"
            "3\n00:00:10,000 --> 00:00:14,000\n오늘 뭐 할까요?\n"
        )

        from submerge.merge import MergeConfig

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=18,
            layout="top-bottom",
        )
        output_path, warnings = merge_bilingual(sub1, sub2, output, config)

        subs = pysubs2.load(str(output_path))
        n_bottom = sum(1 for e in subs if e.style == "bottom")
        n_top = sum(1 for e in subs if e.style == "top")

        # 3 German events from each of two sources = 6 bottom events total
        # Dedup should remove some — bottom count must be < 6
        assert n_bottom < 6, f"Expected bottom events < 6, got {n_bottom}"
        assert n_top == 3
        assert output_path.exists()
