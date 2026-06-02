"""Unit tests for probe.py — external tool error handling and parsing."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from submerge.probe import (
    NoSubtitleTracksError,
    ProbeError,
    SubtitleTrack,
    find_track_by_language,
    list_subtitle_tracks,
)


class TestListSubtitleTracks:
    """Tests for list_subtitle_tracks."""

    def test_raises_when_file_not_found(self, tmp_path):
        """Error if video file doesn't exist."""
        with pytest.raises(ProbeError, match="File not found"):
            list_subtitle_tracks(tmp_path / "nonexistent.mkv")

    def test_raises_when_ffprobe_not_found(self, tmp_path):
        """Clear error if ffprobe is not on PATH."""
        video = tmp_path / "test.mkv"
        video.touch()
        with (
            patch("subprocess.run", side_effect=FileNotFoundError()),
            pytest.raises(ProbeError, match="ffprobe not found"),
        ):
            list_subtitle_tracks(video)

    def test_raises_when_ffprobe_fails(self, tmp_path):
        """Clear error if ffprobe returns non-zero exit code."""
        video = tmp_path / "test.mkv"
        video.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "ffprobe", stderr="Invalid data"
            )
            with pytest.raises(ProbeError, match="ffprobe failed"):
                list_subtitle_tracks(video)

    def test_raises_when_no_subtitle_streams(self, tmp_path):
        """Error if ffprobe returns no streams."""
        video = tmp_path / "test.mkv"
        video.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"streams": []}')
            with pytest.raises(NoSubtitleTracksError, match="No subtitle tracks found"):
                list_subtitle_tracks(video)

    def test_returns_tracks_on_valid_json(self, tmp_path):
        """Returns SubtitleTrack list when ffprobe returns valid JSON."""
        video = tmp_path / "test.mkv"
        video.touch()
        mock_json = """
        {"streams": [
            {"index": 2, "codec_name": "subrip", "tags": {"language": "eng", "title": "English"},
             "disposition": {"default": 1, "forced": 0}},
            {"index": 3, "codec_name": "ass", "tags": {"language": "fre"},
             "disposition": {"default": 0, "forced": 0}}
        ]}
        """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_json)
            tracks = list_subtitle_tracks(video)
        assert len(tracks) == 2
        assert tracks[0].codec == "subrip"
        assert tracks[0].language == "eng"
        assert tracks[0].is_default is True
        assert tracks[1].codec == "ass"
        assert tracks[1].language == "fre"

    def test_raises_when_no_text_tracks(self, tmp_path):
        """Error when only image-based subtitles are present."""
        video = tmp_path / "test.mkv"
        video.touch()
        mock_json = """
        {"streams": [
            {"index": 0, "codec_name": "hdmv_pgs_subtitle",
             "tags": {}, "disposition": {}}
        ]}
        """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_json)
            with pytest.raises(NoSubtitleTracksError, match="No text subtitle tracks"):
                list_subtitle_tracks(video)

    def test_raises_on_json_decode_error(self, tmp_path):
        """Error if ffprobe returns invalid JSON."""
        video = tmp_path / "test.mkv"
        video.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json")
            with pytest.raises(ProbeError, match="JSON parsing error"):
                list_subtitle_tracks(video)

    def test_raises_on_ffprobe_timeout(self, tmp_path):
        """Error if ffprobe times out."""
        video = tmp_path / "test.mkv"
        video.touch()
        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 30)),
            pytest.raises(ProbeError, match="ffprobe timeout"),
        ):
            list_subtitle_tracks(video)


class TestSubtitleTrackDisplay:
    """Tests for SubtitleTrack.display_name."""

    def test_display_name_with_title(self):
        track = SubtitleTrack(0, "subrip", "eng", "English SDH", False, False, True)
        name = track.display_name
        assert "#0" in name
        assert "[eng]" in name
        assert "English SDH" in name

    def test_display_name_with_flags(self):
        track = SubtitleTrack(1, "ass", "fre", None, True, True, True)
        name = track.display_name
        assert "#1" in name
        assert "[default, forced]" in name


class TestFindTrackByLanguage:
    """Tests for find_track_by_language."""

    def test_finds_exact_match(self):
        tracks = [SubtitleTrack(0, "subrip", "eng", None, False, False, True)]
        result = find_track_by_language(tracks, "eng")
        assert result is not None
        assert result.language == "eng"

    def test_matches_2char_to_3char(self):
        tracks = [SubtitleTrack(0, "subrip", "eng", None, False, False, True)]
        result = find_track_by_language(tracks, "en")
        assert result is not None
        assert result.language == "eng"

    def test_matches_3char_to_2char(self):
        tracks = [SubtitleTrack(0, "subrip", "en", None, False, False, True)]
        result = find_track_by_language(tracks, "eng")
        assert result is not None
        assert result.language == "en"

    def test_skips_non_text_tracks(self):
        tracks = [SubtitleTrack(0, "hdmv_pgs_subtitle", "eng", None, False, False, False)]
        result = find_track_by_language(tracks, "eng")
        assert result is None

    def test_returns_none_when_no_match(self):
        tracks = [SubtitleTrack(0, "subrip", "fre", None, False, False, True)]
        result = find_track_by_language(tracks, "eng")
        assert result is None
