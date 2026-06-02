"""Tests for the CLI module.

Tests kept: input validation and user error messages.
Tests removed: Click framework tests, redundant options.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from submerge.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestExtractCommand:
    """Tests for extract command."""

    def test_requires_output_option(self, runner, tmp_path: Path):
        """extract requires --output option."""
        video = tmp_path / "test.mkv"
        video.touch()

        result = runner.invoke(main, ["extract", str(video)])

        assert result.exit_code != 0
        assert "output" in result.output.lower() or "required" in result.output.lower()

    def test_error_when_video_not_found(self, runner, tmp_path: Path):
        """extract shows error if video file doesn't exist."""
        result = runner.invoke(
            main,
            [
                "extract",
                str(tmp_path / "nonexistent.mkv"),
                "-o",
                str(tmp_path / "output.srt"),
            ],
        )

        assert result.exit_code != 0

    @patch("submerge.cli.extract_subtitles")
    def test_extracts_with_track_index(self, mock_extract, runner, tmp_path: Path):
        """extract passes track index to extract_subtitles."""
        video = tmp_path / "test.mkv"
        video.touch()
        output = tmp_path / "output.srt"

        mock_extract.return_value = output

        result = runner.invoke(
            main,
            [
                "extract",
                str(video),
                "-o",
                str(output),
                "--track",
                "2",
            ],
        )

        assert result.exit_code == 0
        mock_extract.assert_called_once()
        call_kwargs = mock_extract.call_args
        assert call_kwargs[1]["track_index"] == 2

    @patch("submerge.cli.extract_subtitles")
    def test_extracts_with_language(self, mock_extract, runner, tmp_path: Path):
        """extract passes language to extract_subtitles."""
        video = tmp_path / "test.mkv"
        video.touch()
        output = tmp_path / "output.srt"

        mock_extract.return_value = output

        result = runner.invoke(
            main,
            [
                "extract",
                str(video),
                "-o",
                str(output),
                "--lang",
                "en",
            ],
        )

        assert result.exit_code == 0
        mock_extract.assert_called_once()
        call_kwargs = mock_extract.call_args
        assert call_kwargs[1]["language"] == "en"

    @patch("submerge.cli.extract_subtitles")
    def test_shows_error_when_no_subtitle_tracks(self, mock_extract, runner, tmp_path: Path):
        """extract shows clear error when no subtitle tracks found."""
        from submerge.probe import NoSubtitleTracksError

        video = tmp_path / "test.mkv"
        video.touch()

        mock_extract.side_effect = NoSubtitleTracksError("No subtitle tracks found")

        result = runner.invoke(
            main,
            [
                "extract",
                str(video),
                "-o",
                str(tmp_path / "output.srt"),
            ],
        )

        assert result.exit_code == 1
        assert "no subtitle tracks" in result.output.lower()

    @patch("submerge.cli.extract_subtitles")
    def test_shows_error_when_extraction_fails(self, mock_extract, runner, tmp_path: Path):
        """extract shows clear error when extraction fails."""
        from submerge.extract import SubtitleExtractionError

        video = tmp_path / "test.mkv"
        video.touch()

        mock_extract.side_effect = SubtitleExtractionError("ffmpeg failed")

        result = runner.invoke(
            main,
            [
                "extract",
                str(video),
                "-o",
                str(tmp_path / "output.srt"),
            ],
        )

        assert result.exit_code == 1
        assert (
            "ffmpeg failed" in result.output.lower() or "extraction error" in result.output.lower()
        )  # noqa: E501

    @patch("submerge.cli.extract_subtitles")
    def test_shows_error_when_probe_fails(self, mock_extract, runner, tmp_path: Path):
        """extract shows clear error when probe fails."""
        from submerge.probe import ProbeError

        video = tmp_path / "test.mkv"
        video.touch()

        mock_extract.side_effect = ProbeError("ffprobe not found")

        result = runner.invoke(
            main,
            [
                "extract",
                str(video),
                "-o",
                str(tmp_path / "output.srt"),
            ],
        )

        assert result.exit_code == 1
        assert "ffprobe" in result.output.lower() or "error" in result.output.lower()


class TestSyncCommand:
    """Tests for sync command."""

    def test_requires_ref_or_video(self, runner, sample_srt_fr: Path):
        """sync requires --ref or --video."""
        result = runner.invoke(main, ["sync", str(sample_srt_fr), "-o", "output.srt"])
        assert result.exit_code != 0
        assert "ref" in result.output.lower() or "video" in result.output.lower()

    @patch("submerge.cli.check_ffsubsync_available")
    def test_shows_install_message_when_ffsubsync_missing(
        self, mock_check, runner, tmp_path: Path, sample_srt_fr: Path, sample_srt_pl: Path
    ):
        """Clear message if ffsubsync is not installed."""
        mock_check.return_value = False

        result = runner.invoke(
            main,
            [
                "sync",
                str(sample_srt_fr),
                "--ref",
                str(sample_srt_pl),
                "-o",
                str(tmp_path / "output.srt"),
            ],
        )

        assert result.exit_code == 1
        assert "ffsubsync" in result.output
        assert "pip install" in result.output


class TestMergeCommand:
    """Tests for merge command."""

    def test_creates_ass_file(
        self,
        runner,
        tmp_path: Path,
        sample_srt_fr: Path,
        sample_srt_pl: Path,
    ):
        """merge creates an ASS file."""
        output = tmp_path / "output.ass"

        result = runner.invoke(
            main, ["merge", str(sample_srt_fr), str(sample_srt_pl), "-o", str(output)]
        )

        assert result.exit_code == 0
        assert output.exists()

    def test_rejects_invalid_color(
        self,
        runner,
        tmp_path: Path,
        sample_srt_fr: Path,
        sample_srt_pl: Path,
    ):
        """merge rejects invalid colors."""
        result = runner.invoke(
            main,
            [
                "merge",
                str(sample_srt_fr),
                str(sample_srt_pl),
                "-o",
                str(tmp_path / "out.ass"),
                "--color1",
                "invalid",
            ],
        )

        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_accepts_layout_option(
        self,
        runner,
        tmp_path: Path,
        sample_srt_fr: Path,
        sample_srt_pl: Path,
    ):
        """merge accepts --layout stacked."""
        output = tmp_path / "output.ass"

        result = runner.invoke(
            main,
            [
                "merge",
                str(sample_srt_fr),
                str(sample_srt_pl),
                "-o",
                str(output),
                "--layout",
                "stacked",
            ],
        )

        assert result.exit_code == 0
        assert output.exists()
