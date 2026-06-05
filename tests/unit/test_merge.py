"""Tests for the merge module.

Tests kept: observable behavior (file created, correct content).
Tests removed: pysubs2 implementation details (alignments, internal styles).
Complete integration tests are in tests/integration/.
"""

from __future__ import annotations

from pathlib import Path

import pysubs2
import pytest

from submerge.merge import (
    InvalidSubtitleError,
    MergeConfig,
    merge_bilingual,
)


class TestMergeBilingual:
    """Tests for merge_bilingual - observable behavior."""

    def test_creates_ass_file(self, tmp_path: Path, sample_srt_fr: Path, sample_srt_pl: Path):
        """Merge creates an ASS file."""
        output = tmp_path / "output.ass"

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        merge_bilingual(sample_srt_fr, sample_srt_pl, output, config)

        assert output.exists()
        assert output.suffix == ".ass"

    def test_contains_all_events(self, tmp_path: Path, sample_srt_fr: Path, sample_srt_pl: Path):
        """ASS file contains all events from both sources."""
        output = tmp_path / "output.ass"

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        merge_bilingual(sample_srt_fr, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        fr_subs = pysubs2.load(str(sample_srt_fr))
        pl_subs = pysubs2.load(str(sample_srt_pl))

        assert len(subs) == len(fr_subs) + len(pl_subs)

    def test_events_sorted_by_time(self, tmp_path: Path, sample_srt_fr: Path, sample_srt_pl: Path):
        """Events are sorted by start time."""
        output = tmp_path / "output.ass"

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        merge_bilingual(sample_srt_fr, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        times = [e.start for e in subs]
        assert times == sorted(times)

    def test_invalid_file_raises_error(self, tmp_path: Path, sample_srt_pl: Path):
        """Invalid file raises InvalidSubtitleError."""
        invalid_file = tmp_path / "invalid.srt"
        invalid_file.write_text("This is not a valid subtitle file")

        with pytest.raises(InvalidSubtitleError):
            merge_bilingual(invalid_file, sample_srt_pl, tmp_path / "output.ass")

    def test_missing_file_raises_error(self, tmp_path: Path, sample_srt_pl: Path):
        """Missing file raises error."""
        with pytest.raises(InvalidSubtitleError, match="not found"):
            merge_bilingual(
                tmp_path / "nonexistent.srt",
                sample_srt_pl,
                tmp_path / "output.ass",
            )


class TestMergeConfig:
    """Tests for MergeConfig - default values."""

    def test_default_values(self):
        """Verify config default values."""
        config = MergeConfig()

        assert config.color_bottom == "#FFFFFF"
        assert config.color_top == "#FFFF00"
        assert config.fontsize_bottom == 22
        assert config.fontsize_top == 22
        assert config.font_bottom == ""
        assert config.outline_bottom == 2.0
        assert config.outline_top == 2.0
        assert config.shadow_bottom == 1.0
        assert config.shadow_top == 1.0
        assert config.layout == "top-bottom"

    def test_per_style_fontsize(self, tmp_path: Path, sample_srt_fr: Path, sample_srt_pl: Path):
        """merge_bilingual applies distinct fontsize_bottom/fontsize_top."""
        config = MergeConfig(
            fontsize_bottom=22,
            fontsize_top=16,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        output = tmp_path / "output.ass"

        merge_bilingual(sample_srt_fr, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        assert subs.styles["bottom"].fontsize == 22
        assert subs.styles["top"].fontsize == 16


class TestDeduplication:
    """Tests for event deduplication in merge_bilingual."""

    def test_deduplicate_ass_events(self, tmp_path: Path, sample_srt_pl: Path):
        """Duplicate events in input are collapsed to one in output."""
        # Create an SRT with two identical events at the same timestamp
        dup_srt = tmp_path / "dup.srt"
        dup_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n2\n00:00:01,000 --> 00:00:02,000\nHello\n"
        )
        output = tmp_path / "output.ass"

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        merge_bilingual(dup_srt, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        # Only one "Hello" event should remain for bottom style
        bottom_events = [e for e in subs if e.style == "bottom"]
        hello_events = [e for e in bottom_events if e.plaintext == "Hello"]
        assert len(hello_events) == 1


class TestInlineTagCleanup:
    """Tests for inline alignment tag stripping in merge_bilingual."""

    def test_bottom_style_not_overridden_by_inline_tag(self, tmp_path: Path, sample_srt_pl: Path):
        """Inline {\an8} tag is stripped from bottom events."""
        # Create an SRT with {\an8} tag in one event
        tagged_srt = tmp_path / "tagged.srt"
        tagged_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n{\\an8}Hallo Welt\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nNormaler Text\n"
        )
        output = tmp_path / "output.ass"

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        merge_bilingual(tagged_srt, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        bottom_events = [e for e in subs if e.style == "bottom"]
        tagged_events = [e for e in bottom_events if "Hallo Welt" in e.plaintext]
        assert len(tagged_events) == 1
        assert "\\an" not in tagged_events[0].text
        assert tagged_events[0].style == "bottom"


class TestLinebreakStripping:
    """Tests for \\N and \\n linebreak stripping in bottom events."""

    def test_bottom_newline_stripped(self, tmp_path: Path, sample_srt_pl: Path):
        r"""\\N in bottom event text is replaced with a space."""
        nl_srt = tmp_path / "newline.srt"
        nl_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nErste Zeile\\NZweite Zeile\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nNormal\n"
        )
        output = tmp_path / "output.ass"

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        merge_bilingual(nl_srt, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        bottom_events = [e for e in subs if e.style == "bottom"]
        tagged = [e for e in bottom_events if "Erste Zeile" in e.plaintext]
        assert len(tagged) == 1
        assert "\\N" not in tagged[0].text
        assert "Erste Zeile Zweite Zeile" in tagged[0].text

    def test_top_newline_preserved(self, tmp_path: Path, sample_srt_pl: Path):
        r"""\\N in top event text is kept."""
        nl_srt = tmp_path / "newline_top.srt"
        nl_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nLinia pierwsza\\NLinia druga\n\n")
        top_srt = tmp_path / "top.srt"
        top_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTop Event\n")
        output = tmp_path / "output.ass"

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        # sub1 = bottom, sub2 = top. Put the newline in sub2 (top).
        merge_bilingual(top_srt, nl_srt, output, config)

        subs = pysubs2.load(str(output))
        top_events = [e for e in subs if e.style == "top"]
        assert len(top_events) >= 1
        found = next((e for e in top_events if "Linia" in e.plaintext), None)
        assert found is not None
        assert "\\N" in found.text

    def test_soft_newline_stripped_from_bottom(self, tmp_path: Path, sample_srt_pl: Path):
        r"""\\n (soft newline) in bottom event text is also stripped."""
        nl_srt = tmp_path / "soft_nl.srt"
        nl_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nPart one\\nPart two\n\n")
        output = tmp_path / "output.ass"

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        merge_bilingual(nl_srt, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        bottom_events = [e for e in subs if e.style == "bottom"]
        tagged = [e for e in bottom_events if "Part one" in e.plaintext]
        assert len(tagged) == 1
        assert "\\n" not in tagged[0].text
        assert "Part one Part two" in tagged[0].text


class TestReMerge:
    """Tests for re-merge / output reuse behaviour."""

    def test_remerge_overwrites_existing_ass(self, tmp_path: Path, sample_srt_pl: Path):
        """merge_bilingual overwrites an existing .ass output file."""
        fr_srt = tmp_path / "Movie.de.srt"
        fr_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nNeuer Text\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nZweite Zeile\n"
        )
        output = tmp_path / "Movie.de-ko.ass"
        # Pre-existing stale output
        output.write_text("old content")

        config = MergeConfig(
            fontsize_bottom=20,
            fontsize_top=20,
            outline_bottom=2.0,
            outline_top=2.0,
        )
        merge_bilingual(fr_srt, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        assert subs is not None
        assert "old content" not in output.read_text()
