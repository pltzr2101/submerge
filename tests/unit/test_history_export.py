"""Tests for history export (ZIP download) endpoint."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from submerge.config import get_settings_for_test
from submerge.queue import (
    dequeue,
    enqueue,
    get_history_entries_by_ids,
    init_db,
)


class TestGetHistoryEntriesByIds:
    def test_returns_empty_for_no_matches(self, tmp_path):
        """Non-existing IDs return empty list."""
        (tmp_path / "config").mkdir(exist_ok=True)
        s = get_settings_for_test(pairs="de-ko", config_dir=str(tmp_path / "config"))
        init_db(s)
        result = get_history_entries_by_ids([999, 888], settings=s)
        assert result == []

    def test_returns_only_done_entries(self, tmp_path):
        """Only 'done' status entries are returned, not 'failed' or 'pending'."""
        media = tmp_path / "media"
        config = tmp_path / "config"
        media.mkdir(exist_ok=True)
        config.mkdir(exist_ok=True)
        s = get_settings_for_test(
            pairs="de-ko", media_root=str(media), config_dir=str(config)
        )
        init_db(s)

        v1 = media / "done.mkv"
        v2 = media / "failed.mkv"
        v1.touch()
        v2.touch()

        enqueue(v1, s)
        enqueue(v2, s)
        dequeue(v1, "done", output_files=["/tmp/test.ass"], settings=s)
        dequeue(v2, "failed", "error", settings=s)

        all_entries = get_history_entries_by_ids([1, 2], settings=s)
        assert len(all_entries) == 1
        assert all_entries[0]["id"] == 1

    def test_returns_output_files_parsed(self, tmp_path):
        """output_files JSON is correctly parsed to Python list."""
        media = tmp_path / "media"
        config = tmp_path / "config"
        media.mkdir(exist_ok=True)
        config.mkdir(exist_ok=True)
        s = get_settings_for_test(
            pairs="de-ko", media_root=str(media), config_dir=str(config)
        )
        init_db(s)

        v = media / "test.mkv"
        v.touch()
        enqueue(v, s)
        dequeue(
            v,
            "done",
            output_files=["/tmp/test.de-ko.ass", "/tmp/test.en-de.ass"],
            settings=s,
        )

        result = get_history_entries_by_ids([1], settings=s)
        assert result[0]["output_files"] == [
            "/tmp/test.de-ko.ass",
            "/tmp/test.en-de.ass",
        ]


class TestHistoryExportApi:
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
        settings = get_settings()

        import importlib

        from submerge import api as api_module
        from submerge.queue import init_db as queue_init_db

        importlib.reload(api_module)
        queue_init_db(settings)

        from starlette.testclient import TestClient

        return TestClient(api_module.app), settings

    def test_export_with_existing_files(self, tmp_path, monkeypatch):
        """Happy path: ZIP response with status 200 and Content-Disposition."""
        client, settings = self._make_client(tmp_path, monkeypatch)

        media = Path(settings.media_root)
        v = media / "test.mkv"
        v.touch()

        # Create fixture .ass files
        ass1 = media / "test.de-ko.ass"
        ass2 = media / "test.en-de.ass"
        ass1.write_text("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
        ass2.write_text("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

        enqueue(v, settings)
        dequeue(
            v,
            "done",
            output_files=[str(ass1), str(ass2)],
            settings=settings,
        )

        resp = client.get("/api/history/export?ids=1")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]

        # Verify ZIP contents
        zip_data = io.BytesIO(resp.content)
        with zipfile.ZipFile(zip_data, "r") as zf:
            names = zf.namelist()
            assert "test.de-ko.ass" in names
            assert "test.en-de.ass" in names

    def test_export_empty_ids(self, tmp_path, monkeypatch):
        """Empty ids parameter returns 400."""
        client, _ = self._make_client(tmp_path, monkeypatch)
        resp = client.get("/api/history/export?ids=")
        assert resp.status_code == 400

    def test_export_no_matching_done(self, tmp_path, monkeypatch):
        """IDs without done status return 404."""
        client, settings = self._make_client(tmp_path, monkeypatch)

        media = Path(settings.media_root)
        v = media / "test.mkv"
        v.touch()
        enqueue(v, settings)
        dequeue(v, "failed", "error", settings=settings)

        resp = client.get("/api/history/export?ids=1")
        assert resp.status_code == 404

    def test_export_too_many_ids(self, tmp_path, monkeypatch):
        """More than 50 IDs returns 400."""
        client, _ = self._make_client(tmp_path, monkeypatch)
        ids = ",".join(str(i) for i in range(1, 52))
        resp = client.get(f"/api/history/export?ids={ids}")
        assert resp.status_code == 400

    def test_export_invalid_ids(self, tmp_path, monkeypatch):
        """Non-numeric IDs return 400."""
        client, _ = self._make_client(tmp_path, monkeypatch)
        resp = client.get("/api/history/export?ids=abc,def")
        assert resp.status_code == 400

    def test_export_path_outside_media_root(self, tmp_path, monkeypatch):
        """Files outside media_root are skipped without HTTP 500."""
        client, settings = self._make_client(tmp_path, monkeypatch)

        media = Path(settings.media_root)
        v = media / "test.mkv"
        v.touch()

        # File outside media_root
        outside_path = tmp_path / "outside.ass"
        outside_path.write_text("outside")

        # File inside media_root
        inside_path = media / "inside.ass"
        inside_path.write_text("inside")

        enqueue(v, settings)
        dequeue(
            v,
            "done",
            output_files=[str(outside_path), str(inside_path)],
            settings=settings,
        )

        resp = client.get("/api/history/export?ids=1")
        assert resp.status_code == 200  # Outside path is skipped, inside still included
        zip_data = io.BytesIO(resp.content)
        with zipfile.ZipFile(zip_data, "r") as zf:
            names = zf.namelist()
            assert "inside.ass" in names
            assert "outside.ass" not in names
