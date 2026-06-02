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

        merge_bilingual(sample_srt_fr, sample_srt_pl, output)

        assert output.exists()
        assert output.suffix == ".ass"

    def test_contains_all_events(self, tmp_path: Path, sample_srt_fr: Path, sample_srt_pl: Path):
        """ASS file contains all events from both sources."""
        output = tmp_path / "output.ass"

        merge_bilingual(sample_srt_fr, sample_srt_pl, output)

        subs = pysubs2.load(str(output))
        fr_subs = pysubs2.load(str(sample_srt_fr))
        pl_subs = pysubs2.load(str(sample_srt_pl))

        assert len(subs) == len(fr_subs) + len(pl_subs)

    def test_events_sorted_by_time(self, tmp_path: Path, sample_srt_fr: Path, sample_srt_pl: Path):
        """Events are sorted by start time."""
        output = tmp_path / "output.ass"

        merge_bilingual(sample_srt_fr, sample_srt_pl, output)

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
        assert config.fontsize == 20
        assert config.fontsize_bottom == 20
        assert config.fontsize_top == 18
        assert config.font_name == "Roboto"
        assert config.outline == 2.0
        assert config.outline_bottom == 2.0
        assert config.outline_top == 2.0
        assert config.shadow == 0.0
        assert config.layout == "top-bottom"

    def test_per_style_fontsize(self, tmp_path: Path, sample_srt_fr: Path, sample_srt_pl: Path):
        """merge_bilingual applies distinct fontsize_bottom/fontsize_top."""
        config = MergeConfig(
            fontsize=14,
            fontsize_bottom=22,
            fontsize_top=16,
        )
        output = tmp_path / "output.ass"

        merge_bilingual(sample_srt_fr, sample_srt_pl, output, config)

        subs = pysubs2.load(str(output))
        assert subs.styles["bottom"].fontsize == 22
        assert subs.styles["top"].fontsize == 16
