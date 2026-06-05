"""Tests for merge history (get_history, clear_history, API endpoints)."""

from __future__ import annotations

from pathlib import Path

import pytest

from submerge.config import get_settings_for_test
from submerge.queue import clear_history, dequeue, enqueue, get_all_entries, get_history, init_db


@pytest.fixture
def history_settings(tmp_path: Path):
    """Settings with tmp_path as media_root and config_dir."""
    (tmp_path / "media").mkdir(exist_ok=True)
    (tmp_path / "config").mkdir(exist_ok=True)
    s = get_settings_for_test(
        pairs="de-ko",
        media_root=str(tmp_path / "media"),
        config_dir=str(tmp_path / "config"),
    )
    init_db(s)
    return s


class TestGetHistory:
    def test_get_history_empty(self, history_settings):
        """Empty DB returns empty list."""
        entries = get_history(settings=history_settings)
        assert entries == []
        assert isinstance(entries, list)

    def test_get_history_returns_done_and_failed(self, tmp_path, history_settings):
        """Only done + failed entries are returned, pending is excluded."""
        v1 = tmp_path / "media" / "done.mkv"
        v2 = tmp_path / "media" / "failed.mkv"
        v3 = tmp_path / "media" / "pending.mkv"
        for v in (v1, v2, v3):
            v.touch()

        enqueue(v1, history_settings)
        enqueue(v2, history_settings)
        enqueue(v3, history_settings)
        dequeue(v1, "done", settings=history_settings)
        dequeue(v2, "failed", "some error", settings=history_settings)
        # v3 stays pending

        entries = get_history(settings=history_settings)
        statuses = {e["status"] for e in entries}
        assert len(entries) == 2
        assert statuses == {"done", "failed"}

    def test_get_history_newest_first(self, tmp_path, history_settings):
        """Entries are returned newest first (by last_checked)."""
        v1 = tmp_path / "media" / "first.mkv"
        v2 = tmp_path / "media" / "second.mkv"
        v3 = tmp_path / "media" / "third.mkv"
        for v in (v1, v2, v3):
            v.touch()

        enqueue(v1, history_settings)
        enqueue(v2, history_settings)
        enqueue(v3, history_settings)
        dequeue(v1, "done", settings=history_settings)
        dequeue(v2, "done", settings=history_settings)
        dequeue(v3, "done", settings=history_settings)

        entries = get_history(settings=history_settings)
        assert len(entries) == 3
        # Newest first means third (v3), second (v2), first (v1)
        names = [e["video_name"] for e in entries]
        assert names == ["third.mkv", "second.mkv", "first.mkv"]

    def test_clear_history_removes_completed(self, tmp_path, history_settings):
        """clear_history removes done/failed, leaves pending."""
        v1 = tmp_path / "media" / "done.mkv"
        v2 = tmp_path / "media" / "pending.mkv"
        for v in (v1, v2):
            v.touch()

        enqueue(v1, history_settings)
        enqueue(v2, history_settings)
        dequeue(v1, "done", settings=history_settings)

        removed = clear_history(settings=history_settings)
        assert removed == 1

        all_entries = get_all_entries(settings=history_settings)
        assert len(all_entries) == 1
        assert all_entries[0]["status"] == "pending"

    def test_history_limit(self, tmp_path, history_settings):
        """limit=N restricts result to at most N entries."""
        for i in range(5):
            v = tmp_path / "media" / f"video{i}.mkv"
            v.touch()
            enqueue(v, history_settings)
            dequeue(v, "done", settings=history_settings)

        entries = get_history(limit=2, settings=history_settings)
        assert len(entries) == 2

    def test_duration_and_output_files_stored(self, tmp_path, history_settings):
        """duration_ms and output_files are persisted and returned."""
        v = tmp_path / "media" / "with_data.mkv"
        v.touch()

        enqueue(v, history_settings)
        dequeue(
            v,
            "done",
            duration_ms=1234,
            output_files=["/media/with_data.de-ko.ass", "/media/with_data.en-de.ass"],
            settings=history_settings,
        )

        entries = get_history(settings=history_settings)
        assert len(entries) == 1
        assert entries[0]["duration_ms"] == 1234
        assert entries[0]["output_files"] == [
            "/media/with_data.de-ko.ass",
            "/media/with_data.en-de.ass",
        ]


class TestHistoryApi:
    @staticmethod
    def _make_client(tmp_path, monkeypatch, pairs="de-ko"):
        """Create a TestClient with isolated config dir."""
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path / "media"))
        monkeypatch.setenv("SUBTOOLS_PAIRS", pairs)
        (tmp_path / "media").mkdir(exist_ok=True)

        from submerge.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()

        import importlib

        from submerge import api as api_module
        from submerge.queue import init_db as queue_init_db

        importlib.reload(api_module)
        queue_init_db(settings)

        from starlette.testclient import TestClient

        return TestClient(api_module.app)

    def test_api_history_endpoint(self, tmp_path, monkeypatch):
        """GET /api/history returns 200 with correct JSON structure."""
        client = self._make_client(tmp_path, monkeypatch)

        resp = client.get("/api/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "count" in data
        assert isinstance(data["entries"], list)

    def test_api_history_clear_endpoint(self, tmp_path, monkeypatch):
        """POST /api/history/clear returns 200 with {status: ok}."""
        client = self._make_client(tmp_path, monkeypatch)

        resp = client.post("/api/history/clear")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "removed" in data
