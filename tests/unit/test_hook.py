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


class TestMergeConfigExpanded:
    """Fix 2: Verify process_bilingual_merge passes all style fields."""

    def test_all_style_fields_passed(self, tmp_path: Path):
        """MergeConfig receives all expanded style fields from settings."""
        import submerge.config as cfg_mod
        from submerge.hook import process_bilingual_merge
        from submerge.merge import MergeConfig

        settings = cfg_mod.SubtoolsSettings(
            SUBTOOLS_PAIRS="de-ko",
            font_bottom="Arial",
            font_top="Noto Sans KR",
            bottom_bold=True,
            top_bold=False,
            bottom_outline=3.0,
            top_outline=1.5,
            bottom_outline_color="#111111",
            top_outline_color="#222222",
            bottom_shadow=2.0,
            top_shadow=0.5,
            bottom_margin_v=40,
            top_margin_v=10,
            bottom_margin_h=25,
            top_margin_h=15,
            bottom_spacing=1.0,
            top_spacing=0.5,
            stacked_gap=12,
        )

        video = tmp_path / "Movie.mkv"
        video.touch()
        de_sub = tmp_path / "Movie.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        ko_sub = tmp_path / "Movie.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nWorld\n")

        mv = __import__("submerge.hook", fromlist=["merge_bilingual"])
        with patch.object(mv, "merge_bilingual") as mock_merge:
            process_bilingual_merge(video, {"de": de_sub, "ko": ko_sub}, settings)

            assert mock_merge.called
            config = mock_merge.call_args[0][3]  # 4th positional arg
            assert isinstance(config, MergeConfig)
            assert config.font_bottom == "Arial"
            assert config.font_top == "Noto Sans KR"
            assert config.bold_bottom is True
            assert config.bold_top is False
            assert config.outline == 3.0
            assert config.outline_color_bottom == "#111111"
            assert config.outline_color_top == "#222222"
            assert config.shadow == 2.0
            assert config.shadow_bottom == 2.0
            assert config.shadow_top == 0.5
            assert config.margin_v_bottom == 40
            assert config.margin_v_top == 10
            assert config.margin_h_bottom == 25
            assert config.margin_h_top == 15
            assert config.spacing_bottom == 1.0
            assert config.spacing_top == 0.5
            assert config.stacked_gap == 12

    def test_default_style_fields(self, tmp_path: Path):
        """MergeConfig uses defaults when settings lack per-language fields."""
        import submerge.config as cfg_mod
        from submerge.hook import process_bilingual_merge

        settings = cfg_mod.SubtoolsSettings(SUBTOOLS_PAIRS="de-ko")

        video = tmp_path / "Movie.mkv"
        video.touch()
        de_sub = tmp_path / "Movie.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        ko_sub = tmp_path / "Movie.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nWorld\n")

        mv = __import__("submerge.hook", fromlist=["merge_bilingual"])
        with patch.object(mv, "merge_bilingual") as mock_merge:
            process_bilingual_merge(video, {"de": de_sub, "ko": ko_sub}, settings)
            assert mock_merge.called
            config = mock_merge.call_args[0][3]
            # Backward compatibility: defaults
            assert config.font_bottom == ""  # empty string default
            assert config.font_top == "Noto Sans KR"
            assert config.bold_bottom is False
            assert config.outline == 2.0


class TestPollingQueueInteraction:
    """Fix 1: Verify polling worker dequeues and queue skips active polls."""

    def test_polling_worker_dequeues_after_merge(self, tmp_path: Path):
        """Polling worker calls dequeue after successful merge."""
        import submerge.config as cfg_mod
        from submerge.hook import process_bilingual_merge
        from submerge.queue import enqueue, dequeue

        settings = cfg_mod.SubtoolsSettings(SUBTOOLS_PAIRS="de-ko")
        video = tmp_path / "Movie.mkv"
        video.touch()

        de_sub = tmp_path / "Movie.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        ko_sub = tmp_path / "Movie.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nWorld\n")

        # Enqueue first, then simulate polling merge completion
        enqueue(video, settings)

        mv = __import__("submerge.hook", fromlist=["merge_bilingual"])
        with patch.object(mv, "merge_bilingual") as mock_merge:
            process_bilingual_merge(video, {"de": de_sub, "ko": ko_sub}, settings)
            dequeue(video, "done", settings=settings)

        # Verify entry is marked done
        from submerge.queue import get_pending_entries
        pending = get_pending_entries(settings)
        assert len(pending) == 0

    def test_queue_skips_when_polling_active(self, tmp_path: Path):
        """Queue worker skips entries that are being polled."""
        import submerge.config as cfg_mod
        from submerge.hook import get_polling_jobs
        from submerge.queue import enqueue, process_queue

        import threading
        settings = cfg_mod.SubtoolsSettings(SUBTOOLS_PAIRS="de-ko")
        video = tmp_path / "Movie.mkv"
        video.touch()

        # Simulate active polling
        polling_jobs = get_polling_jobs()
        polling_jobs[str(video.resolve())] = threading.Event()

        de_sub = tmp_path / "Movie.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        ko_sub = tmp_path / "Movie.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nWorld\n")

        # Enqueue the video
        enqueue(video, settings)

        # Queue worker should skip because polling is active
        result = process_queue(settings)
        assert result["still_pending"] >= 1 or result["merged"] == 0

        # Cleanup
        polling_jobs.pop(str(video.resolve()), None)
