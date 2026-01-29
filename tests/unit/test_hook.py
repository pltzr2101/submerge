"""Tests for the hook module.

Tests kept: important behaviors and error handling.
Tests removed: trivial functions (1-2 lines), implementation details.
Integration tests for complete flow are in tests/integration/.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from submerge.hook import (
    InvalidLanguageError,
    ProcessingError,
    find_subtitle_path,
    process_hook,
)


class TestFindSubtitlePath:
    """Tests for find_subtitle_path - non-trivial behaviors."""

    def test_finds_hi_fallback(self, tmp_path: Path):
        """Finds .hi.srt fallback if regular .srt doesn't exist."""
        video = tmp_path / "Show.mkv"
        video.touch()
        hi_sub = tmp_path / "Show.fr.hi.srt"
        hi_sub.touch()

        result = find_subtitle_path(video, "fr")
        assert result == hi_sub

    def test_prefers_regular_over_hi(self, tmp_path: Path):
        """Prefers regular .srt over .hi.srt if both exist."""
        video = tmp_path / "Show.mkv"
        video.touch()
        regular = tmp_path / "Show.fr.srt"
        regular.touch()
        hi_sub = tmp_path / "Show.fr.hi.srt"
        hi_sub.touch()

        result = find_subtitle_path(video, "fr")
        assert result == regular


class TestProcessHook:
    """Tests for process_hook - error handling."""

    def test_invalid_lang_raises_error(self, tmp_path: Path, settings_fr_pl_en):
        """Unconfigured language raises InvalidLanguageError."""
        video = tmp_path / "Show.mkv"
        video.touch()
        sub = tmp_path / "Show.de.srt"
        sub.touch()

        with pytest.raises(InvalidLanguageError, match="Invalid language: de"):
            process_hook(video, sub, "de", settings_fr_pl_en)

    def test_missing_video_raises_error(self, tmp_path: Path, settings_fr_pl_en):
        """Missing video raises ProcessingError."""
        video = tmp_path / "nonexistent.mkv"
        sub = tmp_path / "Show.fr.srt"
        sub.touch()

        with pytest.raises(ProcessingError, match="not found"):
            process_hook(video, sub, "fr", settings_fr_pl_en)

    @patch("submerge.hook.process_bilingual_merge")
    def test_calls_merge_when_all_langs_present(
        self, mock_merge: MagicMock, tmp_path: Path, settings_fr_pl_en
    ):
        """Calls merge when all languages are present."""
        video = tmp_path / "Show.mkv"
        video.touch()
        fr = tmp_path / "Show.fr.srt"
        pl = tmp_path / "Show.pl.srt"
        en = tmp_path / "Show.en.srt"
        fr.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        pl.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        en.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")

        fr_pl = tmp_path / "Show.fr-pl.ass"
        en_pl = tmp_path / "Show.en-pl.ass"
        mock_merge.return_value = [fr_pl, en_pl]

        result = process_hook(video, fr, "fr", settings_fr_pl_en)

        assert result.status == "merged"
        mock_merge.assert_called_once()
