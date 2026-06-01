"""Tests for modules that wrap external tools (ffmpeg, ffprobe, ffsubsync).

These tests only verify error handling.
We don't test that external tools work (that would be E2E tests).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from submerge.extract import SubtitleExtractionError, extract_subtitles
from submerge.probe import ProbeError, list_subtitle_tracks
from submerge.sync import FfsubsyncNotFoundError, SyncError, sync_subtitles

# =============================================================================
# Tests probe.py
# =============================================================================

class TestProbe:
    """Tests for list_subtitle_tracks."""

    def test_raises_when_file_not_found(self, tmp_path: Path):
        """Error if video file doesn't exist."""
        with pytest.raises(ProbeError, match="File not found"):
            list_subtitle_tracks(tmp_path / "nonexistent.mkv")


# =============================================================================
# Tests extract.py
# =============================================================================

class TestExtract:
    """Tests for extract_subtitles."""

    def test_raises_when_ffmpeg_not_found(self, tmp_path: Path):
        """Clear error if ffmpeg is not installed."""
        video_file = tmp_path / "test.mkv"
        video_file.touch()

        with patch("submerge.extract.list_subtitle_tracks") as mock_probe:
            from submerge.probe import SubtitleTrack
            mock_probe.return_value = [
                SubtitleTrack(2, "subrip", "eng", None, False, True, True),
            ]

            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError()

                with pytest.raises(SubtitleExtractionError, match="ffmpeg not found"):
                    extract_subtitles(video_file, tmp_path / "output.srt", track_index=2)

    def test_raises_when_output_file_empty(self, tmp_path: Path):
        """Error if extracted file is empty."""
        video_file = tmp_path / "test.mkv"
        video_file.touch()
        output_file = tmp_path / "output.srt"

        with patch("submerge.extract.list_subtitle_tracks") as mock_probe:
            from submerge.probe import SubtitleTrack
            mock_probe.return_value = [
                SubtitleTrack(2, "subrip", "eng", None, False, True, True),
            ]

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                output_file.write_text("")

                with pytest.raises(SubtitleExtractionError, match="Extracted file is empty"):
                    extract_subtitles(video_file, output_file, track_index=2)


# =============================================================================
# Tests sync.py
# =============================================================================

class TestSync:
    """Tests for sync_subtitles."""

    def test_raises_when_ffsubsync_not_installed(self, tmp_path: Path):
        """Clear error if ffsubsync is not installed."""
        ref_file = tmp_path / "reference.srt"
        ref_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nRef\n")

        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")

        with patch("shutil.which", return_value=None):
            with pytest.raises(FfsubsyncNotFoundError, match="ffsubsync not found"):
                sync_subtitles(ref_file, input_file, tmp_path / "output.srt")

    def test_raises_when_reference_file_missing(self, tmp_path: Path):
        """Error if reference file doesn't exist."""
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n")

        with patch("shutil.which", return_value="/usr/bin/ffs"):
            with pytest.raises(SyncError, match="File not found"):
                sync_subtitles(
                    tmp_path / "nonexistent.srt",
                    input_file,
                    tmp_path / "output.srt",
                )

    def test_raises_when_format_not_supported(self, tmp_path: Path):
        """Error if format is not supported."""
        ref_file = tmp_path / "reference.txt"
        ref_file.write_text("Not a subtitle file")

        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n")

        with patch("shutil.which", return_value="/usr/bin/ffs"):
            with pytest.raises(SyncError, match="Unsupported format"):
                sync_subtitles(ref_file, input_file, tmp_path / "output.srt")
