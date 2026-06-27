"""Tests for media type detection logic (mirrors JS getMediaType in history.html).

The Python implementation matches the JavaScript logic exactly:
- Paths with /movies/ segment → 'movies'
- Paths with /tv/ segment → 'tv'
- Everything else → 'unknown'
"""

from __future__ import annotations


def get_media_type(video_path: str) -> str:
    """Re-implementation of the JS getMediaType function from history.html."""
    p = (video_path or "").replace("\\", "/")
    if "/movies/" in p:
        return "movies"
    if "/tv/" in p:
        return "tv"
    return "unknown"


class TestMediaTypeDetection:
    def test_movies_path_detection(self):
        """Path with /movies/ → 'movies'."""
        path = "/data/media/movies/The Drama (2026) [imdbid-tt33071426]/The Drama.mkv"
        assert get_media_type(path) == "movies"

    def test_tv_path_detection(self):
        """Path with /tv/ → 'tv'."""
        path = (
            "/data/media/tv/A Knight of the Seven Kingdoms (2026)"
            " [tvdbid-433631]/Season 01/ep01.mkv"
        )
        assert get_media_type(path) == "tv"

    def test_unknown_path_detection(self):
        """Path without /movies/ or /tv/ → 'unknown'."""
        path = "/data/media/other/something.mkv"
        assert get_media_type(path) == "unknown"

    def test_windows_path_normalization(self):
        """Windows path with \\movies\\ is normalized to /movies/."""
        path = r"C:\data\media\movies\The Drama\The Drama.mkv"
        assert get_media_type(path) == "movies"

    def test_empty_path(self):
        """None or empty path returns 'unknown'."""
        assert get_media_type("") == "unknown"

    def test_path_with_movies_in_filename_only(self):
        """/movies/ must be a path segment, not just part of a filename."""
        path = "/data/media/shows/the_movies_2026.mkv"
        assert get_media_type(path) == "unknown"

    def test_path_with_tv_in_filename_only(self):
        """/tv/ must be a path segment, not just part of a filename."""
        path = "/data/media/shows/my_tv_show.mkv"
        assert get_media_type(path) == "unknown"


class TestCombinedFilter:
    """Tests for combined status + type filter logic (replicates renderTable behavior)."""

    @staticmethod
    def filter_entries(entries, status_filter="all", type_filter="all"):
        """Replicate the two-stage filter from renderTable()."""
        filtered = list(entries)
        if status_filter != "all":
            filtered = [e for e in filtered if e["status"] == status_filter]
        if type_filter != "all":
            filtered = [e for e in filtered if get_media_type(e["video_path"]) == type_filter]
        return filtered

    @staticmethod
    def _make_entry(status, video_path, video_name="test.mkv"):
        return {"id": 1, "status": status, "video_path": video_path, "video_name": video_name}

    def test_combined_filter_movies_x_done(self):
        """movies + done returns only movies entries with status 'done'."""
        entries = [
            self._make_entry("done", "/data/movies/m1/m1.mkv", "m1.mkv"),
            self._make_entry("failed", "/data/movies/m2/m2.mkv", "m2.mkv"),
            self._make_entry("done", "/data/tv/t1/s01/e01.mkv", "t1.mkv"),
            self._make_entry("done", "/data/other/o1.mkv", "o1.mkv"),
        ]
        result = self.filter_entries(entries, status_filter="done", type_filter="movies")
        assert len(result) == 1
        assert result[0]["video_name"] == "m1.mkv"

    def test_combined_filter_tv_x_all_status(self):
        """tv + status='all' returns all TV entries regardless of status."""
        entries = [
            self._make_entry("done", "/data/tv/t1/s01/e01.mkv", "t1.mkv"),
            self._make_entry("failed", "/data/tv/t2/s01/e01.mkv", "t2.mkv"),
            self._make_entry("pending", "/data/tv/t3/s01/e01.mkv", "t3.mkv"),
            self._make_entry("done", "/data/movies/m1/m1.mkv", "m1.mkv"),
        ]
        result = self.filter_entries(entries, status_filter="all", type_filter="tv")
        assert len(result) == 3
        assert all("t" in e["video_name"] for e in result)

    def test_type_filter_all_shows_unknown(self):
        """type='all' includes entries with unknown media type."""
        entries = [
            self._make_entry("done", "/data/movies/m1/m1.mkv", "m1.mkv"),
            self._make_entry("done", "/data/other/o1.mkv", "o1.mkv"),
            self._make_entry("done", "/data/unknown/u1.mkv", "u1.mkv"),
        ]
        result = self.filter_entries(entries, status_filter="all", type_filter="all")
        assert len(result) == 3

    def test_type_filter_movies_excludes_unknown(self):
        """movies filter excludes entries without /movies/ in path."""
        entries = [
            self._make_entry("done", "/data/movies/m1/m1.mkv", "m1.mkv"),
            self._make_entry("done", "/data/other/o1.mkv", "o1.mkv"),
        ]
        result = self.filter_entries(entries, status_filter="all", type_filter="movies")
        assert len(result) == 1
        assert result[0]["video_name"] == "m1.mkv"

    def test_type_filter_tv_excludes_unknown(self):
        """tv filter excludes entries without /tv/ in path."""
        entries = [
            self._make_entry("done", "/data/tv/t1/s01/e01.mkv", "t1.mkv"),
            self._make_entry("done", "/data/other/o1.mkv", "o1.mkv"),
        ]
        result = self.filter_entries(entries, status_filter="all", type_filter="tv")
        assert len(result) == 1
        assert result[0]["video_name"] == "t1.mkv"
