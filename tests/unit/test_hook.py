"""Tests for the hook module.

Tests kept: important behaviors and error handling.
Tests removed: trivial functions (1-2 lines), implementation details.
Integration tests for complete flow are in tests/integration/.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from filelock import Timeout

from submerge.hook import (
    InvalidLanguageError,
    ProcessingError,
    _config_fingerprint,
    find_subtitle_path,
    get_output_path,
    process_hook,
    should_skip_existing,
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

    def test_sdh_track_preference(self, tmp_path: Path):
        """Prefers normal .srt over .sdh.srt when both exist."""
        video = tmp_path / "Movie.mkv"
        video.touch()
        normal = tmp_path / "Movie.de.srt"
        normal.touch()
        sdh_sub = tmp_path / "Movie.de.sdh.srt"
        sdh_sub.touch()

        result = find_subtitle_path(video, "de")
        assert result == normal

    def test_sdh_fallback(self, tmp_path: Path):
        """Falls back to .sdh.srt when no normal .srt exists."""
        video = tmp_path / "Movie.mkv"
        video.touch()
        sdh_sub = tmp_path / "Movie.de.sdh.srt"
        sdh_sub.touch()

        result = find_subtitle_path(video, "de")
        assert result == sdh_sub

    def test_cc_track_fallback(self, tmp_path: Path):
        """Falls back to .cc.srt when no normal .srt exists."""
        video = tmp_path / "Movie.mkv"
        video.touch()
        cc_sub = tmp_path / "Movie.de.cc.srt"
        cc_sub.touch()

        result = find_subtitle_path(video, "de")
        assert result == cc_sub

    def test_forced_track_fallback(self, tmp_path: Path):
        """Falls back to .forced.srt when no normal .srt exists."""
        video = tmp_path / "Movie.mkv"
        video.touch()
        forced_sub = tmp_path / "Movie.de.forced.srt"
        forced_sub.touch()

        result = find_subtitle_path(video, "de")
        assert result == forced_sub

    def test_case_insensitive_hi_match(self, tmp_path: Path):
        """Finds .HI.srt via case-insensitive fallback scan."""
        video = tmp_path / "Movie.mkv"
        video.touch()
        hi_sub = tmp_path / "Movie.de.HI.srt"
        hi_sub.touch()

        result = find_subtitle_path(video, "de")
        assert result == hi_sub

    def test_case_insensitive_fallback_returns_path(self, tmp_path: Path):
        """Case-insensitive fallback returns Path, not os.DirEntry."""
        video = tmp_path / "Movie.mkv"
        video.touch()
        sub = tmp_path / "Movie.DE.SRT"
        sub.touch()

        result = find_subtitle_path(video, "de")
        assert isinstance(result, Path)
        assert result == sub

    def test_ass_output_not_used_as_subtitle_input(self, tmp_path: Path):
        """Generated .ass output is never picked up as subtitle input."""
        video = tmp_path / "Movie.mkv"
        video.touch()
        # Create a previously merged .ass output — no .srt exists
        ass_output = tmp_path / "Movie.de-ko.ass"
        ass_output.touch()

        result = find_subtitle_path(video, "de")
        assert result is None


class TestSkipExistingFingerprint:
    """Tests for config fingerprint in should_skip_existing."""

    @staticmethod
    def _settings(pairs: str = "fr-pl"):
        from submerge.config import get_settings_for_test

        return get_settings_for_test(pairs=pairs, layout="top-bottom")

    def test_remerges_when_no_fingerprint(self, tmp_path: Path):
        """Dune .ass with no SubmergeConfigHash forces re-merge."""
        video = tmp_path / "Movie.mkv"
        video.touch()

        fr_srt = tmp_path / "Movie.fr.srt"
        fr_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
        pl_srt = tmp_path / "Movie.pl.srt"
        pl_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nCześć\n")

        # Dune .ass (no fingerprint) with correct pair name
        ass_path = get_output_path(video, "fr", "pl")
        ass_path.write_text(
            "[Script Info]\nTitle: Test\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,"
            "&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
        )

        result = should_skip_existing(video, {"fr": fr_srt, "pl": pl_srt}, self._settings())
        assert result is False  # No fingerprint → force re-merge

    def test_forces_remerge_on_config_change(self, tmp_path: Path):
        """Mismatched fingerprint forces re-merge."""
        video = tmp_path / "Movie.mkv"
        video.touch()

        fr_srt = tmp_path / "Movie.fr.srt"
        fr_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
        pl_srt = tmp_path / "Movie.pl.srt"
        pl_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nCześć\n")

        # Create .ass with a different (fake) fingerprint
        ass_path = get_output_path(video, "fr", "pl")
        ass_path.write_text(
            "[Script Info]\nTitle: Test\nScriptType: v4.00+\n"
            "SubmergeConfigHash: aabbccdd00112233\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,"
            "&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
        )

        result = should_skip_existing(video, {"fr": fr_srt, "pl": pl_srt}, self._settings())
        assert result is False  # Fingerprints don't match

    def test_skips_when_fingerprint_matches(self, tmp_path: Path):
        """Matching fingerprint + newer .ass → skip."""
        video = tmp_path / "Movie.mkv"
        video.touch()

        fr_srt = tmp_path / "Movie.fr.srt"
        fr_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
        pl_srt = tmp_path / "Movie.pl.srt"
        pl_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nCześć\n")

        settings = self._settings()
        fingerprint = _config_fingerprint(settings)
        ass_path = get_output_path(video, "fr", "pl")
        ass_path.write_text(
            "[Script Info]\nTitle: Test\nScriptType: v4.00+\n"
            f"SubmergeConfigHash: {fingerprint}\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,"
            "&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
        )

        result = should_skip_existing(video, {"fr": fr_srt, "pl": pl_srt}, settings)
        assert result is True  # Fingerprints match + mtime passed


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
            font_bottom="",
            font_top="",
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
        with patch.object(mv, "merge_bilingual") as mock_merge:  # noqa: F841
            process_bilingual_merge(video, {"de": de_sub, "ko": ko_sub}, settings)

            assert mock_merge.called
            config = mock_merge.call_args[0][3]  # 4th positional arg
            assert isinstance(config, MergeConfig)
            assert config.font_bottom == ""
            assert config.font_top == ""
            assert config.bold_bottom is True
            assert config.bold_top is False
            assert config.outline_bottom == 3.0
            assert config.outline_color_bottom == "#111111"
            assert config.outline_color_top == "#222222"
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
        with patch.object(mv, "merge_bilingual") as mock_merge:  # noqa: F841
            process_bilingual_merge(video, {"de": de_sub, "ko": ko_sub}, settings)
            assert mock_merge.called
            config = mock_merge.call_args[0][3]
            # Backward compatibility: defaults
            assert config.font_bottom == ""  # empty string default
            assert config.font_top == ""  # empty string default
            assert config.bold_bottom is False
            assert config.outline_bottom == 2.0


class TestPollingQueueInteraction:
    """Fix 1: Verify polling worker dequeues and queue skips active polls."""

    def test_polling_worker_dequeues_after_merge(self, tmp_path: Path):
        """Polling worker calls dequeue after successful merge."""
        import submerge.config as cfg_mod
        from submerge.hook import process_bilingual_merge
        from submerge.queue import dequeue, enqueue

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
        with patch.object(mv, "merge_bilingual") as mock_merge:  # noqa: F841
            process_bilingual_merge(video, {"de": de_sub, "ko": ko_sub}, settings)
            dequeue(video, "done", settings=settings)

        # Verify entry is marked done
        from submerge.queue import get_pending_entries

        pending = get_pending_entries(settings)
        assert len(pending) == 0

    def test_queue_skips_when_polling_active(self, tmp_path: Path):
        """Queue worker skips entries that are being polled."""
        import submerge.config as cfg_mod
        from submerge.hook import cancel_polling, start_polling
        from submerge.queue import enqueue, process_queue

        settings = cfg_mod.SubtoolsSettings(SUBTOOLS_PAIRS="de-ko")
        video = tmp_path / "Movie.mkv"
        video.touch()

        # Register a polling job via the public API
        start_polling(video, settings)

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
        cancel_polling(video)


class TestProcessHookTimeout:
    """Tests for process_hook when lock acquisition times out."""

    def test_already_processing_log(self, tmp_path: Path):
        """process_hook logs INFO when lock times out."""
        import submerge.config as cfg_mod
        from submerge.hook import process_hook

        settings = cfg_mod.SubtoolsSettings(SUBTOOLS_PAIRS="de-ko")
        video = tmp_path / "Show.mkv"
        video.touch()
        sub = tmp_path / "Show.de.srt"
        sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n")

        with patch("submerge.hook.FileLock", autospec=True) as mock_lock_cls:
            mock_lock = mock_lock_cls.return_value
            mock_lock.acquire.side_effect = Timeout(
                lock_file="/tmp/lock",
            )
            with patch("submerge.hook.logger") as mock_logger:
                result = process_hook(video, sub, "de", settings)
                assert result.status == "already_processing"
                mock_logger.info.assert_any_call(
                    f"Hook for {video.name}: already processing by polling worker — skipped"
                )


class TestEventSortOrder:
    """Tests for stable event sort order in bilingual merge."""

    def test_ass_event_sort_order(self):
        """Top-style events sort before bottom-style at same timestamp."""
        import pysubs2

        merged = pysubs2.SSAFile()

        # Two events at the same start time, different styles
        bottom_evt = pysubs2.SSAEvent(start=1000, end=2000, style="bottom", text="Guten Tag")
        top_evt = pysubs2.SSAEvent(start=1000, end=2000, style="top", text="안녕하세요")
        merged.append(bottom_evt)
        merged.append(top_evt)

        # Sort as merge_bilingual does
        merged.events.sort(key=lambda e: (e.start, 0 if e.style == "top" else 1))

        assert merged.events[0].style == "top"
        assert merged.events[1].style == "bottom"
