"""Tests for /api/stats endpoint and get_stats function."""

from __future__ import annotations

from pathlib import Path

import pytest

from submerge.config import get_settings_for_test
from submerge.queue import dequeue, enqueue, get_stats, init_db


class TestGetStats:
    @staticmethod
    def _make_settings(tmp_path, media_path=None):
        """Create isolated settings for stats tests."""
        config = tmp_path / "config"
        media = media_path or tmp_path / "media"
        config.mkdir(exist_ok=True)
        media.mkdir(exist_ok=True)
        s = get_settings_for_test(
            pairs="de-ko", media_root=str(media), config_dir=str(config)
        )
        init_db(s)
        return s, media

    def test_empty_db(self, tmp_path):
        """Empty DB returns zeros and None for oldest_pending."""
        s, _ = self._make_settings(tmp_path)
        stats = get_stats(settings=s)
        assert stats["total_merged"] == 0
        assert stats["total_failed"] == 0
        assert stats["total_pending"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["avg_retries"] == 0.0
        assert stats["oldest_pending_hours"] is None
        assert "generated_at" in stats

    def test_known_entries(self, tmp_path):
        """Known DB entries produce correct aggregated stats."""
        s, media = self._make_settings(tmp_path)

        # 3 done, 2 failed, 1 pending
        for i in range(3):
            v = media / f"done_{i}.mkv"
            v.touch()
            enqueue(v, s)
            dequeue(v, "done", settings=s)

        for i in range(2):
            v = media / f"failed_{i}.mkv"
            v.touch()
            enqueue(v, s)
            dequeue(v, "failed", "error", settings=s)

        v = media / "pending.mkv"
        v.touch()
        enqueue(v, s)

        stats = get_stats(settings=s)
        assert stats["total_merged"] == 3
        assert stats["total_failed"] == 2
        assert stats["total_pending"] == 1
        assert stats["success_rate"] == 0.6  # 3/(3+2)
        assert stats["oldest_pending_hours"] is not None
        assert "generated_at" in stats

    def test_only_failed(self, tmp_path):
        """Only failed entries give success_rate=0.0."""
        s, media = self._make_settings(tmp_path)

        v = media / "failed.mkv"
        v.touch()
        enqueue(v, s)
        dequeue(v, "failed", "error", settings=s)

        stats = get_stats(settings=s)
        assert stats["total_merged"] == 0
        assert stats["total_failed"] == 1
        assert stats["success_rate"] == 0.0

    def test_only_pending(self, tmp_path):
        """Only pending entries give oldest_pending_hours > 0."""
        s, media = self._make_settings(tmp_path)

        v = media / "pending.mkv"
        v.touch()
        enqueue(v, s)

        stats = get_stats(settings=s)
        assert stats["total_pending"] == 1
        assert stats["oldest_pending_hours"] is not None
        assert stats["oldest_pending_hours"] >= 0


class TestStatsApi:
    @staticmethod
    def _make_client(tmp_path, monkeypatch, pairs="de-ko"):
        """Create a TestClient with isolated config."""
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path / "media"))
        monkeypatch.setenv("SUBTOOLS_PAIRS", pairs)
        (tmp_path / "media").mkdir(exist_ok=True)
        (tmp_path / "config").mkdir(exist_ok=True)

        from submerge.config import get_settings

        get_settings.cache_clear()

        import importlib

        from submerge import api as api_module
        from submerge.queue import init_db as queue_init_db

        importlib.reload(api_module)
        settings = get_settings()
        queue_init_db(settings)

        from starlette.testclient import TestClient

        return TestClient(api_module.app)

    def test_api_stats_returns_200(self, tmp_path, monkeypatch):
        """GET /api/stats returns 200 with all fields."""
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        for field in [
            "total_merged",
            "total_failed",
            "total_pending",
            "success_rate",
            "avg_retries",
            "oldest_pending_hours",
            "generated_at",
        ]:
            assert field in data
