"""Tests for the media scanner module."""

from __future__ import annotations

from pathlib import Path

import pytest

from submerge.config import get_settings_for_test
from submerge.scanner import (
    _is_video_file,
    entry_to_dict,
    find_videos_needing_merge,
    scan_directory,
)


class TestIsVideoFile:
    """Tests for video file detection."""

    def test_recognizes_mkv(self):
        assert _is_video_file(Path("movie.mkv"))

    def test_recognizes_mp4(self):
        assert _is_video_file(Path("movie.mp4"))

    def test_rejects_srt(self):
        assert not _is_video_file(Path("movie.srt"))

    def test_rejects_ass(self):
        assert not _is_video_file(Path("movie.ass"))

    def test_rejects_directory(self):
        assert not _is_video_file(Path("/some/dir"))


class TestScanDirectory:
    """Tests for directory scanning."""

    def test_empty_directory(self, tmp_path: Path):
        settings = get_settings_for_test(pairs="de-ko")
        entries = scan_directory(tmp_path, settings)
        assert entries == []

    def test_finds_video_files(self, tmp_path: Path):
        settings = get_settings_for_test(pairs="de-ko")
        (tmp_path / "Movie.mkv").touch()

        entries = scan_directory(tmp_path, settings)
        assert len(entries) == 1
        assert entries[0].video_name == "Movie.mkv"

    def test_detects_present_and_missing_subs(self, tmp_path: Path):
        """Scanner detects which subtitle languages are present."""
        settings = get_settings_for_test(pairs="de-ko,en-de")
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()
        (tmp_path / "Show.en.srt").touch()
        # ko is missing

        entries = scan_directory(tmp_path, settings)
        assert len(entries) == 1
        st = entries[0].subtitle_status
        assert st["de"]["present"] is True
        assert st["en"]["present"] is True
        assert st["ko"]["present"] is False

    def test_detects_merged_status(self, tmp_path: Path):
        """Scanner detects which pairs have been merged."""
        settings = get_settings_for_test(pairs="de-ko")
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()
        (tmp_path / "Show.ko.srt").touch()
        # No .ass file

        entries = scan_directory(tmp_path, settings)
        assert entries[0].all_langs_present is True
        assert entries[0].all_merged is False

        # Create merged file
        (tmp_path / "Show.de-ko.ass").touch()
        entries = scan_directory(tmp_path, settings)
        assert entries[0].all_merged is True

    def test_uses_relative_parent_dir(self, tmp_path: Path):
        """Scanner reports relative parent directory."""
        settings = get_settings_for_test(pairs="de-ko")
        subdir = tmp_path / "Series" / "Season 1"
        subdir.mkdir(parents=True)
        (subdir / "S01E01.mkv").touch()

        entries = scan_directory(tmp_path, settings)
        assert len(entries) == 1
        assert entries[0].parent_dir == "Series/Season 1"

    def test_handles_nonexistent_root(self):
        """Scanner returns empty list for nonexistent directory."""
        settings = get_settings_for_test(pairs="de-ko")
        entries = scan_directory("/nonexistent/path", settings)
        assert entries == []

    def test_respects_3_letter_lang_codes(self, tmp_path: Path):
        """Scanner finds subtitles with 3-letter codes (e.g., deu.srt)."""
        settings = get_settings_for_test(pairs="de-ko")
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.deu.srt").touch()
        (tmp_path / "Show.kor.srt").touch()

        entries = scan_directory(tmp_path, settings)
        assert len(entries) == 1
        assert entries[0].subtitle_status["de"]["present"] is True
        assert entries[0].subtitle_status["ko"]["present"] is True


class TestFindVideosNeedingMerge:
    """Tests for finding videos needing merge."""

    def test_finds_unmerged_videos(self, tmp_path: Path):
        settings = get_settings_for_test(pairs="de-ko")
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()
        (tmp_path / "Show.ko.srt").touch()

        entries = find_videos_needing_merge(tmp_path, settings)
        assert len(entries) == 1

    def test_excludes_merged_videos(self, tmp_path: Path):
        settings = get_settings_for_test(pairs="de-ko")
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()
        (tmp_path / "Show.ko.srt").touch()
        (tmp_path / "Show.de-ko.ass").touch()

        entries = find_videos_needing_merge(tmp_path, settings)
        assert len(entries) == 0

    def test_excludes_incomplete_videos(self, tmp_path: Path):
        settings = get_settings_for_test(pairs="de-ko")
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()
        # No ko.srt

        entries = find_videos_needing_merge(tmp_path, settings)
        assert len(entries) == 0


class TestEntryToDict:
    """Tests for JSON serialization."""

    def test_serializes_all_fields(self, tmp_path: Path):
        settings = get_settings_for_test(pairs="de-ko")
        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.de.srt").touch()

        entries = scan_directory(tmp_path, settings)
        d = entry_to_dict(entries[0], settings)

        assert d["video_name"] == "Show.mkv"
        assert d["video_path"] == str(video)
        assert "pairs" in d
        assert "de-ko" in d["pairs"]
        assert "required_langs" in d
        assert "de" in d["required_langs"]
        assert "ko" in d["required_langs"]
        assert "subtitle_status" in d
        assert "merged_status" in d
