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
    """Python port of normalizeSearch(). Strips all non-alphanumeric chars."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


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

    def test_dp_no_dots(self):
        assert normalize_search("DP") == "dp"

    def test_spaces_and_hyphens(self):
        assert normalize_search("Frieren S01E03") == "frierens01e03"

    def test_underscores(self):
        assert normalize_search("My_Show_S03") == "myshows03"

    def test_empty(self):
        assert normalize_search("") == ""

    def test_only_special(self):
        assert normalize_search(" .-_") == ""

    def test_brackets_and_apostrophe(self):
        # Parens, apostrophes, hyphens — all non-alphanumeric stripped
        assert normalize_search("Frieren - Beyond Journey's End (2023) - S01E03") == (
            "frierenbeyondjourneysend2023s01e03"
        )

    def test_square_brackets(self):
        assert normalize_search("[HorribleSubs] Show - 01 [1080p].mkv") == (
            "horriblesubsshow011080pmkv"
        )

    def test_leading_trailing_special(self):
        assert normalize_search("___D.P.___") == "dp"


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


class TestSearchFilter:
    """Python port of the search filter logic in renderTable()."""

    def _apply_search(self, entries, query):
        """Replicate the search filter from renderTable()."""
        nq = normalize_search(query)
        result = []
        for e in entries:
            n_name = normalize_search(e["video_name"])
            n_file = normalize_search(
                (e.get("video_path", "") or "").replace("\\", "/").split("/")[-1]
            )
            if len(nq) <= 2:
                info = parse_tv_path(e.get("video_path", ""))
                n_series = (
                    normalize_search(info["seriesName"]) if info and info["seriesName"] else ""
                )
                if (n_series and nq in n_series) or n_name.startswith(nq) or n_file.startswith(nq):
                    result.append(e)
            else:
                info = parse_tv_path(e.get("video_path", ""))
                n_series = (
                    normalize_search(info["seriesName"]) if info and info["seriesName"] else ""
                )
                if nq in n_name or (n_series and nq in n_series) or nq in n_file:
                    result.append(e)
        return result

    def test_long_query_matches_bracketed_name(self):
        # "frierens01e03" should match a filename with special chars
        # — normaliseSearch strips all non-[a-z0-9], so brackets vanish
        entries = [
            {
                "video_name": (
                    "Frieren - S01E03 - Killing Magic [Bluray-1080p][FLAC 2.0][x265]-FROGE.mkv"
                ),
                "video_path": "/tv/Frieren/Season 1/f.mkv",
                "type": "tv",
            },
            {
                "video_name": "Arcane S01E03.mkv",
                "video_path": "/tv/Arcane/Season 1/a.mkv",
                "type": "tv",
            },
        ]
        result = self._apply_search(entries, "frierens01e03")
        assert len(result) == 1
        assert "Frieren" in result[0]["video_name"]

    def test_short_query_dp_matches_series_only(self):
        # "dp" (length 2 ≤ 3) should match D.P. series but NOT DeepSea
        entries = [
            {
                "video_name": "D.P. - S01E01.mkv",
                "video_path": "/tv/D.P./Season 1/e01.mkv",
                "type": "tv",
            },
            {
                "video_name": "kdrama_deep_sea.mkv",
                "video_path": "/tv/DeepSea/Season 1/e01.mkv",
                "type": "tv",
            },
        ]
        result = self._apply_search(entries, "dp")
        # Should only match D.P. (seriesName="D.P." → "dp"), not DeepSea
        assert len(result) == 1
        assert "D.P." in result[0]["video_name"]

    def test_short_query_starts_with_match(self):
        """Short query threshold is now ≤2.  Three-char queries like 'bre'
        use the long-query path (full includes), so this test uses a
        query whose length is 2 to stay in the short-query path."""
        entries = [
            {
                "video_name": "An.Show.S01E01.mkv",
                "video_path": "/tv/An Show/Season 1/e01.mkv",
                "type": "tv",
            },
            {
                "video_name": "AnotherShow.S01E01.mkv",
                "video_path": "/tv/AnotherShow/Season 1/e01.mkv",
                "type": "tv",
            },
        ]
        result = self._apply_search(entries, "an")
        # "an" (len 2 ≤ 2): short-query path
        # nSeries "anshow" includes "an" → entry 1 matches via seriesName
        # nName "anshows01e01" startsWith "an" → entry 1 matches
        # nSeries "anothershow" includes "an" → entry 2 also matches via seriesName!
        # But "an" and "another" are different series — both will match seriesName.
        # The actual renderTable handles this at a different layer (grouping).
        # Here we just verify both pass the filter.
        assert len(result) == 2

    def test_short_query_starts_with_isolated(self):
        """startsWith branch: seriesName does NOT contain the query, but
        video_name starts with it after normalisation — proves the
        ``n_name.startswith(nq)`` path works independently."""
        entries = [
            {
                # seriesName "XYZ" → "xyz" — does NOT contain "bb"
                # video_name "BBQ.S01E01.mkv" → "bbqs01e01" — startsWith "bb"
                "video_name": "BBQ.S01E01.mkv",
                "video_path": "/tv/XYZ/Season 1/e01.mkv",
                "type": "tv",
            },
            {
                "video_name": "Other.S01E01.mkv",
                "video_path": "/tv/Other/Season 1/e01.mkv",
                "type": "tv",
            },
        ]
        result = self._apply_search(entries, "bb")  # len 2 ≤ 3, short-query path
        assert len(result) == 1
        assert "BBQ" in result[0]["video_name"]

    def test_search_only_matches_when_not_empty(self):
        """Empty query bypasses the filter entirely (outer guard ``if currentSearch``).
        _apply_search is only called for non-empty queries; this test verifies
        that a non-empty query correctly filters entries."""
        entries = [
            {"video_name": "Arcane.S01E01.mkv", "video_path": "/tv/Arcane/S1/e.mkv", "type": "tv"},
            {
                "video_name": "Frieren.S01E01.mkv",
                "video_path": "/tv/Frieren/S1/e.mkv",
                "type": "tv",
            },
        ]
        result = self._apply_search(entries, "frieren")
        assert len(result) == 1
        assert "Frieren" in result[0]["video_name"]

    def test_s01_query_matches_via_includes(self):
        """Query 'S01' (3 chars, now >2 → long-query path) must match episode
        codes like S01E01 in video_name via full includes."""
        entries = [
            {
                "video_name": "Frieren.S01E03.mkv",
                "video_path": "/tv/Frieren/Season 1/f.mkv",
                "type": "tv",
            },
            {
                "video_name": "Arcane.S02E01.mkv",
                "video_path": "/tv/Arcane/Season 2/a.mkv",
                "type": "tv",
            },
        ]
        result = self._apply_search(entries, "S01")
        # nq = "s01" (len 3 > 2 → long-query path, full includes)
        # nName "frierens01e03" contains "s01" → match
        # nName "arcanes02e01" does NOT contain "s01" → no match
        assert len(result) == 1
        assert "Frieren" in result[0]["video_name"]

    def test_combined_query_matches_filename_via_videopath_fallback(self):
        """Query 'frieren S01E03' → nq='frierens01e03' (len 13 > 2).
        If video_name only contains the series name (no episode code),
        the video_path filename fallback ensures the query still matches."""
        entries = [
            {
                # video_name lacks episode code — common with CleanNames=False
                "video_name": "Frieren.mkv",
                "video_path": "/tv/Frieren/Season 1/Frieren.S01E03.1080p.mkv",
                "type": "tv",
            },
            {
                "video_name": "Arcane.S02E01.mkv",
                "video_path": "/tv/Arcane/Season 2/a.mkv",
                "type": "tv",
            },
        ]
        result = self._apply_search(entries, "frieren S01E03")
        # nq = "frierens01e03"
        # Entry 1: nName = "frieren" → does NOT contain "frierens01e03"
        #          nFile = "frierens01e031080pmkv" → CONTAINS "frierens01e03" → match
        # Entry 2: nName = "arcanes02e01" → no, nFile = "amkv" → no
        assert len(result) == 1
        assert "Frieren" in result[0]["video_name"]


class TestCollapseDefault:
    """Simulate loadMedia() collapse-initialisation logic."""

    def _simulate_load(self, entries, seen_series, collapsed_series):
        """Replicate the initialisation loop from loadMedia()."""
        for e in entries:
            if e.get("type") != "tv":
                continue
            info = parse_tv_path(e.get("video_path", ""))
            if info and info["seriesName"] and info["seriesName"] not in seen_series:
                seen_series.add(info["seriesName"])
                collapsed_series.add(info["seriesName"])

    def test_first_load_collapses_all(self):
        entries = [
            {"video_name": "E01", "video_path": "/tv/Frieren/S1/E01.mkv", "type": "tv"},
            {"video_name": "E02", "video_path": "/tv/Arcane/S1/E02.mkv", "type": "tv"},
            {"video_name": "Movie", "video_path": "/movies/M.mkv", "type": "movies"},
        ]
        seen = set()
        collapsed = set()
        self._simulate_load(entries, seen, collapsed)
        assert "Frieren" in collapsed
        assert "Arcane" in collapsed
        assert len(collapsed) == 2

    def test_manual_expand_preserved_across_reload(self):
        entries = [
            {"video_name": "E01", "video_path": "/tv/Show/S1/E01.mkv", "type": "tv"},
        ]
        seen = set()
        collapsed = set()
        # First load
        self._simulate_load(entries, seen, collapsed)
        assert "Show" in collapsed

        # User expands
        collapsed.discard("Show")
        assert "Show" not in collapsed

        # Reload — should NOT re-collapse because Show is already in seen
        self._simulate_load(entries, seen, collapsed)
        assert "Show" not in collapsed

    def test_new_series_collapsed_on_later_load(self):
        entries_first = [
            {"video_name": "E01", "video_path": "/tv/Old/S1/E01.mkv", "type": "tv"},
        ]
        entries_later = entries_first + [
            {"video_name": "E01", "video_path": "/tv/New/S1/E01.mkv", "type": "tv"},
        ]
        seen = set()
        collapsed = set()
        self._simulate_load(entries_first, seen, collapsed)
        collapsed.discard("Old")  # user expanded
        self._simulate_load(entries_later, seen, collapsed)
        assert "Old" not in collapsed  # preserved
        assert "New" in collapsed  # new series, collapsed by default

    def test_removed_series_recollapsed_on_return(self):
        """Known behaviour: if a series disappears from the filesystem and
        later returns, it is *not* automatically re-collapsed because
        _seenSeries already contains its name.  User preference (expanded
        state) is preserved across removals and re-additions.

        This is intentional: the user is in control of collapse state.
        """
        entries = [
            {"video_name": "E01", "video_path": "/tv/Gone/S1/E01.mkv", "type": "tv"},
        ]
        seen = set()
        collapsed = set()
        # First load — series is collapsed by default
        self._simulate_load(entries, seen, collapsed)
        assert "Gone" in collapsed

        # Series disappears from filesystem
        self._simulate_load([], seen, collapsed)
        # Still collapsed because we never expanded it manually
        assert "Gone" in collapsed

        # Series returns — already in _seenSeries, NOT re-added to collapsedSeries
        self._simulate_load(entries, seen, collapsed)
        # Unchanged from first load (never expanded)
        assert "Gone" in collapsed

        # Now simulate user expanding it manually
        collapsed.discard("Gone")
        # Series disappears and returns — user preference preserved
        self._simulate_load(entries, seen, collapsed)
        # Expanded state survives because _seenSeries guards against re-collapse
        assert "Gone" not in collapsed
