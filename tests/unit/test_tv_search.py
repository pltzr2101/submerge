"""Unit tests for TV hierarchy (parseTvPath, normalizeSearch) and group rendering."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Re-implementations of the JS functions for offline testing (portable Python)
# ---------------------------------------------------------------------------


def parse_tv_path(video_path: str) -> dict | None:
    """Python port of parseTvPath(). Uses video file extension heuristic
    to distinguish season folders from episode files. Supports sub-season directories."""
    p = video_path.replace("\\", "/")
    idx = p.find("/tv/")
    if idx == -1:
        return None
    # p[idx+1:] is e.g. "tv/Frieren/Season 1/ep.mkv"
    segments = p[idx + 1 :].split("/")
    # segments[0] = "tv", [1] = series, [2+] = season(s) … file
    result: dict = {"seriesName": None, "seasonName": None}
    if len(segments) > 1:
        result["seriesName"] = segments[1]
    if len(segments) > 2:
        for seg in segments[2:]:
            if re.search(r"\.(mkv|mp4|avi|mov|ts|m4v|wmv|flv|webm)$", seg, re.IGNORECASE):
                break  # hit the episode file
            if result["seasonName"] is None:
                result["seasonName"] = seg
    return result


def normalize_search(s: str) -> str:
    """Python port of normalizeSearch()."""
    return re.sub(r"[\s.\-_]", "", s).lower()


# ---------------------------------------------------------------------------
# Grouping logic — mirrors the renderTable() output structure
# ---------------------------------------------------------------------------


def _build_tv_groups(entries: list) -> dict:
    """Group TV entries by seriesName -> seasonName."""
    groups: dict[str, dict[str, list]] = {}
    for e in entries:
        if e["type"] != "tv":
            continue
        info = parse_tv_path(e["video_path"])
        if not info or not info["seriesName"]:
            continue
        sn = info["seriesName"]
        season = info["seasonName"] or "?"
        groups.setdefault(sn, {}).setdefault(season, []).append(e)
    return groups


def _all_chk_video_count(html: str) -> int:
    return len(re.findall(r'class="chk-video"', html))


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _movie(name, path=None):
    return {
        "video_name": name,
        "video_path": path or f"/mnt/media/movies/{name}",
        "type": "movies",
        "parent_dir": "/mnt/media/movies",
        "all_langs_present": True,
        "all_merged": False,
        "subtitle_status": {},
        "merged_status": {},
    }


def _tv(name, series, season, path=None):
    return {
        "video_name": name,
        "video_path": path or f"/mnt/media/tv/{series}/{season}/{name}",
        "type": "tv",
        "parent_dir": f"/mnt/media/tv/{series}/{season}",
        "all_langs_present": True,
        "all_merged": False,
        "subtitle_status": {},
        "merged_status": {},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseTvPath:
    def test_windows_backslash_path(self):
        r = parse_tv_path(r"C:\media\tv\Frieren\Season 1\Frieren.S01E01.mkv")
        assert r is not None
        assert r["seriesName"] == "Frieren"
        assert r["seasonName"] == "Season 1"

    def test_unix_path(self):
        r = parse_tv_path("/mnt/media/tv/Frieren/Season 2/ep01.mkv")
        assert r is not None
        assert r["seriesName"] == "Frieren"
        assert r["seasonName"] == "Season 2"

    def test_missing_tv_segment(self):
        r = parse_tv_path("/mnt/media/movies/Test.mkv")
        assert r is None

    def test_series_no_season(self):
        r = parse_tv_path("/mnt/media/tv/Frieren/episode.mkv")
        assert r is not None
        assert r["seriesName"] == "Frieren"
        assert r["seasonName"] is None

    def test_deep_path(self):
        r = parse_tv_path("/data/tv/Arcane/Season 3/Subbed/Arcane.S03E01.mp4")
        assert r is not None
        assert r["seriesName"] == "Arcane"
        assert r["seasonName"] == "Season 3"

    def test_season_folder_with_dot(self):
        r = parse_tv_path("/mnt/media/tv/Show/Season.1/ep.mkv")
        assert r is not None
        assert r["seriesName"] == "Show"
        assert r["seasonName"] == "Season.1"

    def test_season_folder_staffel(self):
        r = parse_tv_path("/mnt/media/tv/Show/Staffel.1/ep.mkv")
        assert r is not None
        assert r["seriesName"] == "Show"
        assert r["seasonName"] == "Staffel.1"


class TestNormalizeSearch:
    def test_dots_and_mixed_case(self):
        assert normalize_search("D.P.") == "dp"

    def test_spaces_and_hyphens(self):
        assert normalize_search("Frieren S01E03") == "frierens01e03"

    def test_underscores(self):
        assert normalize_search("My_Show_S03") == "myshows03"

    def test_empty(self):
        assert normalize_search("") == ""

    def test_only_special(self):
        assert normalize_search(" .-_") == ""


class TestGrouping:
    def test_mixed_movies_and_tv(self):
        entries = [
            _movie("Inception"),
            _tv("Frieren E01", "Frieren", "Season 1"),
            _tv("Frieren E02", "Frieren", "Season 1"),
            _tv("Frieren E03", "Frieren", "Season 2"),
            _movie("Tenet"),
            _tv("Arcane E01", "Arcane", "Season 1"),
            _tv("Arcane E02", "Arcane", "Season 1"),
        ]
        groups = _build_tv_groups(entries)
        assert "Frieren" in groups
        assert "Arcane" in groups
        assert "Season 1" in groups["Frieren"]
        assert "Season 2" in groups["Frieren"]
        assert len(groups["Frieren"]["Season 1"]) == 2
        assert len(groups["Frieren"]["Season 2"]) == 1
        assert len(groups["Arcane"]["Season 1"]) == 2

    def test_only_movies(self):
        entries = [_movie("A"), _movie("B")]
        groups = _build_tv_groups(entries)
        assert groups == {}

    def test_unknown_type_ignored(self):
        entries = [{"video_name": "X", "video_path": "/x", "type": "unknown"}]
        groups = _build_tv_groups(entries)
        assert groups == {}
