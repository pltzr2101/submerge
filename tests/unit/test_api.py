"""Tests for the api module.

Tests kept: custom logic (filter, config validation).
Tests removed: mock overload on endpoints (tested in integration).
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    """Configure environment variables for tests."""
    monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl,en-pl")
    from submerge.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestHealthCheckFilter:
    """Tests for health check log filter - custom logic."""

    def test_excludes_health_endpoint(self):
        """Filter excludes /health requests."""
        from submerge.api import HealthCheckFilter

        filter_ = HealthCheckFilter()
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='127.0.0.1 - "GET /health HTTP/1.1" 200',
            args=(),
            exc_info=None,
        )

        assert filter_.filter(record) is False

    def test_allows_other_routes(self):
        """Filter allows other routes through."""
        from submerge.api import HealthCheckFilter

        filter_ = HealthCheckFilter()
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='127.0.0.1 - "POST /hook HTTP/1.1" 200',
            args=(),
            exc_info=None,
        )

        assert filter_.filter(record) is True


class TestConfigValidationAtStartup:
    """Tests for config validation at startup - critical behavior."""

    def test_missing_pairs_starts_gracefully(self, monkeypatch):
        """Server starts gracefully without SUBTOOLS_PAIRS, but /hook returns 503."""
        monkeypatch.delenv("SUBTOOLS_PAIRS", raising=False)
        from submerge.config import get_settings
        get_settings.cache_clear()

        import importlib

        import submerge.api
        importlib.reload(submerge.api)

        # App should still be created (no RuntimeError)
        assert submerge.api.app is not None

        # /hook should return 503 when pairs not configured
        from fastapi.testclient import TestClient
        client = TestClient(submerge.api.app)
        resp = client.post("/hook", data={"video": "/data/test.mkv", "subtitle": "/data/test.de.srt", "lang": "de"})  # noqa: E501
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]["message"]

        get_settings.cache_clear()


class TestValidatePath:
    """Tests for path validation."""

    def test_rejects_relative_path(self):
        """Rejects relative paths."""
        from fastapi import HTTPException

        from submerge.api import validate_path

        with pytest.raises(HTTPException) as exc_info:
            validate_path("relative/path.mkv", "video")

        assert exc_info.value.status_code == 400
        assert "absolute path" in str(exc_info.value.detail["message"])


class TestAsyncEndpoints:
    """v2.0.3: Verify async-correctness of blocking endpoints."""

    def test_api_queue_retry_is_async(self):
        """api_queue_retry must be an async function."""
        import inspect

        from submerge.api import api_queue_retry
        assert inspect.iscoroutinefunction(api_queue_retry)

    def test_background_task_no_lambda(self):
        """api_frame_extract must use direct method call, not lambda."""
        import inspect

        from submerge import api as api_module
        source = inspect.getsource(api_module.api_frame_extract)
        assert "BackgroundTask(lambda" not in source
        assert "BackgroundTask(Path(" in source


class TestPresetDelete:
    """Tests for DELETE /api/presets/{name} endpoint."""

    @staticmethod
    def _make_client(tmp_path, monkeypatch, pairs="fr-pl,en-pl"):
        """Create a TestClient with isolated media_root."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", pairs)
        from submerge.config import get_settings
        get_settings.cache_clear()

        import importlib

        from submerge import api as api_module
        importlib.reload(api_module)

        from starlette.testclient import TestClient
        return TestClient(api_module.app), get_settings

    def test_delete_existing_preset(self, tmp_path, monkeypatch):
        """DELETE an existing custom preset returns 200 with deleted name."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.post("/api/presets", json={
            "name": "test-delete", "styles": {"layout": "stacked"}
        })
        assert resp.status_code == 200

        resp = client.delete("/api/presets/test-delete")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["deleted"] == "test-delete"

        get_settings.cache_clear()

    def test_delete_nonexistent_returns_404(self, tmp_path, monkeypatch):
        """DELETE a non-existing preset returns 404."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.delete("/api/presets/nonexistent-xyz")
        assert resp.status_code == 404

        get_settings.cache_clear()

    def test_delete_default_template_returns_400(self, tmp_path, monkeypatch):
        """DELETE of the active default_template returns 400."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.post("/api/presets", json={
            "name": "my-default", "styles": {"layout": "top-bottom"}
        })
        assert resp.status_code == 200

        resp = client.post("/api/settings/default-template",
                           json={"template": "my-default"})
        assert resp.status_code == 200

        resp = client.delete("/api/presets/my-default")
        assert resp.status_code == 400
        data = resp.json()
        assert "default template" in data.get("detail", {}).get("message", "")

        get_settings.cache_clear()

    def test_delete_builtin_returns_400(self, tmp_path, monkeypatch):
        """DELETE of a built-in preset returns 400."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.delete("/api/presets/Cinema Dark")
        assert resp.status_code == 400

        get_settings.cache_clear()


class TestMergeUnknownKeys:
    """Fix 2: api_merge must filter unknown keys from preset overrides."""

    def test_merge_with_unknown_keys_returns_200(self, tmp_path, monkeypatch):
        """POST /api/merge with a preset containing UI-only keys returns 200."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "de-ko")
        from submerge.config import get_settings
        get_settings.cache_clear()

        import importlib

        from submerge import api as api_module
        importlib.reload(api_module)

        from starlette.testclient import TestClient
        client = TestClient(api_module.app)

        # Create a video file
        video_path = tmp_path / "TestShow.mkv"
        video_path.touch()

        # Save a preset with an unknown UI-only key (topText)
        resp = client.post("/api/presets", json={
            "name": "with-ui-keys",
            "styles": {
                "layout": "top-bottom",
                "topText": "Some preview text",
                "bottom_color": "#FFFFFF",
                "top_color": "#FFD700",
            }
        })
        assert resp.status_code == 200

        # api_merge should NOT 500 — filtering strips unknown keys
        resp = client.post("/api/merge", json={
            "video_path": str(video_path),
            "template": "with-ui-keys",
        })
        assert resp.status_code == 200

        get_settings.cache_clear()
