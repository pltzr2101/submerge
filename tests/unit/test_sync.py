"""Unit tests for sync.py — synchronization logic and utilities."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from submerge.sync import (
    FfsubsyncNotFoundError,
    SyncError,
    SyncResult,
    _parse_offset,
    sync_subtitles,
    sync_subtitles_to_video,
)


class TestSyncSubtitles:
    """Tests for sync_subtitles."""

    def test_raises_when_ffsubsync_not_installed(self, tmp_path: Path):
        """Clear error if ffsubsync is not installed."""
        ref_file = tmp_path / "reference.srt"
        ref_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nRef\n")
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")
        with (
            patch("shutil.which", return_value=None),
            pytest.raises(FfsubsyncNotFoundError, match="ffsubsync not found"),
        ):
            sync_subtitles(ref_file, input_file, tmp_path / "output.srt")

    def test_raises_when_reference_file_missing(self, tmp_path: Path):
        """Error if reference file doesn't exist."""
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n")
        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            pytest.raises(SyncError, match="File not found"),
        ):
            sync_subtitles(tmp_path / "nonexistent.srt", input_file, tmp_path / "output.srt")

    def test_raises_when_input_file_missing(self, tmp_path: Path):
        """Error if input file doesn't exist."""
        ref_file = tmp_path / "reference.srt"
        ref_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nRef\n")
        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            pytest.raises(SyncError, match="File not found"),
        ):
            sync_subtitles(ref_file, tmp_path / "nonexistent.srt", tmp_path / "output.srt")

    def test_raises_when_format_not_supported(self, tmp_path: Path):
        """Error if format is not supported."""
        ref_file = tmp_path / "reference.txt"
        ref_file.write_text("Not a subtitle file")
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n")
        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            pytest.raises(SyncError, match="Unsupported format"),
        ):
            sync_subtitles(ref_file, input_file, tmp_path / "output.srt")

    def test_successful_sync(self, tmp_path: Path):
        """Returns SyncResult on successful ffsubsync run."""
        ref_file = tmp_path / "reference.srt"
        ref_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nRef\n")
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")
        output_file = tmp_path / "output.srt"
        output_file.touch()

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="offset: 500ms", stderr="")
            result = sync_subtitles(ref_file, input_file, output_file)
        assert result.success is True
        assert result.output_path == output_file
        assert result.offset_ms == 500

    def test_raises_when_subprocess_fails(self, tmp_path: Path):
        """Error if ffsubsync returns non-zero exit code."""
        ref_file = tmp_path / "reference.srt"
        ref_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nRef\n")
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")
        output_file = tmp_path / "output.srt"

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = subprocess.CalledProcessError(1, "ffs", stderr="sync error")
            with pytest.raises(SyncError, match="ffsubsync failed"):
                sync_subtitles(ref_file, input_file, output_file)

    def test_raises_when_output_not_created(self, tmp_path: Path):
        """Error if output file wasn't created after ffsubsync run."""
        ref_file = tmp_path / "reference.srt"
        ref_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nRef\n")
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")
        output_file = tmp_path / "output.srt"

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with pytest.raises(SyncError, match="Output file was not created"):
                sync_subtitles(ref_file, input_file, output_file)

    def test_warns_on_large_offset(self, tmp_path: Path, caplog):
        """Logs warning when sync offset exceeds 5000ms."""
        import logging

        ref_file = tmp_path / "reference.srt"
        ref_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nRef\n")
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")
        output_file = tmp_path / "output.srt"
        output_file.touch()

        caplog.set_level(logging.WARNING, logger="submerge.sync")
        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="offset: 6000ms", stderr="")
            sync_subtitles(ref_file, input_file, output_file)
        assert "Large sync offset" in caplog.text


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_instantiate_with_all_fields(self):
        result = SyncResult(success=True, output_path=Path("out.srt"), offset_ms=123)
        assert result.success is True
        assert result.output_path == Path("out.srt")
        assert result.offset_ms == 123

    def test_offset_defaults_to_none(self):
        result = SyncResult(success=True, output_path=Path("out.srt"))
        assert result.offset_ms is None


class TestParseOffset:
    """Tests for _parse_offset helper."""

    def test_parses_offset_format(self):
        assert _parse_offset("offset: 1234ms") == 1234

    def test_parses_negative_offset(self):
        assert _parse_offset("offset: -500ms") == -500

    def test_parses_shift_format(self):
        assert _parse_offset("shift: 2500ms") == 2500

    def test_returns_none_when_no_match(self):
        assert _parse_offset("no offset info here") is None


class TestSyncSubtitlesToVideo:
    """Tests for sync_subtitles_to_video."""

    def test_raises_when_video_missing(self, tmp_path: Path):
        """Error if video file doesn't exist."""
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n")
        output_file = tmp_path / "output.srt"

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            pytest.raises(SyncError, match="Video file not found"),
        ):
            sync_subtitles_to_video(tmp_path / "nonexistent.mkv", input_file, output_file)

    def test_raises_when_input_missing(self, tmp_path: Path):
        """Error if input subtitle file doesn't exist."""
        video = tmp_path / "video.mkv"
        video.touch()
        output_file = tmp_path / "output.srt"

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            pytest.raises(SyncError, match="File not found"),
        ):
            sync_subtitles_to_video(video, tmp_path / "nonexistent.srt", output_file)

    def test_raises_when_format_unsupported(self, tmp_path: Path):
        """Error if input subtitle format is not supported."""
        video = tmp_path / "video.mkv"
        video.touch()
        input_file = tmp_path / "input.txt"
        input_file.write_text("not a subtitle")
        output_file = tmp_path / "output.srt"

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            pytest.raises(SyncError, match="Unsupported format"),
        ):
            sync_subtitles_to_video(video, input_file, output_file)

    def test_successful_sync_to_video(self, tmp_path: Path):
        """Returns SyncResult on successful audio-based sync."""
        video = tmp_path / "video.mkv"
        video.touch()
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")
        output_file = tmp_path / "output.srt"
        output_file.touch()

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="shift: 100ms", stderr="")
            result = sync_subtitles_to_video(video, input_file, output_file)
        assert result.success is True
        assert result.output_path == output_file
        assert result.offset_ms == 100

    def test_raises_when_subprocess_fails_to_video(self, tmp_path: Path):
        """Error if ffsubsync returns non-zero for video sync."""
        video = tmp_path / "video.mkv"
        video.touch()
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")
        output_file = tmp_path / "output.srt"

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = subprocess.CalledProcessError(1, "ffs", stderr="sync error")
            with pytest.raises(SyncError, match="ffsubsync failed"):
                sync_subtitles_to_video(video, input_file, output_file)

    def test_raises_when_output_not_created_to_video(self, tmp_path: Path):
        """Error if output not created after video sync."""
        video = tmp_path / "video.mkv"
        video.touch()
        input_file = tmp_path / "input.srt"
        input_file.write_text("1\n00:00:01,500 --> 00:00:02,500\nInput\n")
        output_file = tmp_path / "output.srt"

        with (
            patch("shutil.which", return_value="/usr/bin/ffs"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with pytest.raises(SyncError, match="Output file was not created"):
                sync_subtitles_to_video(video, input_file, output_file)
