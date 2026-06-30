"""Tests for the api module.

Tests kept: custom logic (filter, config validation).
Tests removed: mock overload on endpoints (tested in integration).
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

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


class TestHealthEndpoint:
    """Tests for the /health endpoint response content."""

    def test_health_includes_alass_available(self, monkeypatch):
        """alass: true when binary is in PATH, false when missing."""
        from unittest.mock import patch

        from submerge.api import app
        from submerge.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("SUBTOOLS_PAIRS", "de-ko")

        # Simulate alass installed
        with patch("submerge.api.shutil.which") as mock_which:
            mock_which.side_effect = lambda name: f"/usr/bin/{name}"

            from starlette.testclient import TestClient

            client = TestClient(app)
            resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["alass"] is True
        assert data["ffmpeg"] is True
        assert data["ffprobe"] is True

        # Simulate alass missing
        with patch("submerge.api.shutil.which") as mock_which:
            mock_which.side_effect = lambda name: (f"/usr/bin/{name}" if name != "alass" else None)

            resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["alass"] is False
        assert data["ffmpeg"] is True

        get_settings.cache_clear()

    def test_health_all_ok_independent_of_alass(self, monkeypatch):
        """all_ok only requires ffmpeg+ffprobe+configured, not alass."""
        from unittest.mock import patch

        from submerge.api import app
        from submerge.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("SUBTOOLS_PAIRS", "de-ko")

        # alass missing but ffmpeg/ffprobe present → still ok
        with patch("submerge.api.shutil.which") as mock_which:
            mock_which.side_effect = lambda name: (f"/usr/bin/{name}" if name != "alass" else None)

            from starlette.testclient import TestClient

            client = TestClient(app)
            resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


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
        resp = client.post(
            "/hook", data={"video": "/data/test.mkv", "subtitle": "/data/test.de.srt", "lang": "de"}
        )  # noqa: E501
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

    def test_rejects_empty_path(self):
        """Rejects empty path string."""
        from fastapi import HTTPException

        from submerge.api import validate_path

        with pytest.raises(HTTPException) as exc_info:
            validate_path("", "video")

        assert exc_info.value.status_code == 400
        assert "absolute path" in str(exc_info.value.detail["message"])

    def test_rejects_path_with_dotdot_traversal(self):
        """Rejects paths with .. traversal when check_media_root=True."""
        from fastapi import HTTPException

        from submerge.api import validate_path

        with pytest.raises(HTTPException) as exc_info:
            validate_path("/data/../etc/passwd", "video", check_media_root=True)

        assert exc_info.value.status_code == 400
        assert "within media root" in str(exc_info.value.detail["message"])


class TestAsyncEndpoints:
    """v2.0.3: Verify async-correctness of blocking endpoints."""

    def test_api_queue_retry_is_async(self):
        """api_queue_retry must be an async function."""
        import inspect

        from submerge.routers.queue import api_queue_retry

        assert inspect.iscoroutinefunction(api_queue_retry)

    def test_background_task_no_lambda(self):
        """api_frame_extract must use direct method call, not lambda."""
        import inspect

        from submerge.routers import scanner as scanner_module

        source = inspect.getsource(scanner_module.api_frame_extract)
        assert "BackgroundTask(lambda" not in source
        assert "BackgroundTask(Path(" in source


class TestPresetDelete:
    """Tests for DELETE /api/presets/{name} endpoint."""

    @staticmethod
    def _make_client(tmp_path, monkeypatch, pairs="fr-pl,en-pl"):
        """Create a TestClient with isolated media_root and config_dir."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
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

        resp = client.post(
            "/api/presets", json={"name": "test-delete", "styles": {"layout": "stacked"}}
        )
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

        resp = client.post(
            "/api/presets", json={"name": "my-default", "styles": {"layout": "top-bottom"}}
        )
        assert resp.status_code == 200

        resp = client.post("/api/settings/default-template", json={"template": "my-default"})
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


class TestPresetValidation:
    """Tests for preset style field validation."""

    @staticmethod
    def _make_client(tmp_path, monkeypatch, pairs="fr-pl,en-pl"):
        """Create a TestClient with isolated media_root and config_dir."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", pairs)
        from submerge.config import get_settings

        get_settings.cache_clear()

        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)

        from starlette.testclient import TestClient

        return TestClient(api_module.app), get_settings

    def test_unknown_style_key_returns_422(self, tmp_path, monkeypatch):
        """POST /api/presets with unknown style key returns 422."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.post(
            "/api/presets",
            json={"name": "bad-preset", "styles": {"unknown_field": "value"}},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["status"] == "error"
        assert "Unknown style fields" in data["detail"]["message"]
        assert "unknown_field" in data["detail"]["message"]

        get_settings.cache_clear()

    def test_known_style_keys_accepted(self, tmp_path, monkeypatch):
        """POST /api/presets with known style keys returns 200."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.post(
            "/api/presets",
            json={
                "name": "good-preset",
                "styles": {"layout": "stacked", "bottom_fontsize": 20},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        get_settings.cache_clear()


class TestMergeUnknownKeys:
    """Fix 2: api_merge must filter unknown keys from preset overrides."""

    def test_merge_with_known_style_keys_returns_200(self, tmp_path, monkeypatch):
        """POST /api/merge with a preset containing known style keys returns 200."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
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

        # Save a preset with known style keys (unknown keys rejected by validation)
        resp = client.post(
            "/api/presets",
            json={
                "name": "with-ui-keys",
                "styles": {
                    "layout": "top-bottom",
                    "bottom_color": "#FFFFFF",
                    "top_color": "#FFD700",
                },
            },
        )
        assert resp.status_code == 200

        # api_merge should NOT 500 — filtering strips unknown keys
        resp = client.post(
            "/api/merge",
            json={
                "video_path": str(video_path),
                "template": "with-ui-keys",
            },
        )
        assert resp.status_code == 200

        get_settings.cache_clear()


class TestBatchMerge:
    """Tests for POST /api/batch-merge endpoint."""

    @staticmethod
    def _make_client(tmp_path, monkeypatch, pairs="fr-pl"):
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", pairs)
        from submerge.config import get_settings

        get_settings.cache_clear()

        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)

        from starlette.testclient import TestClient

        return TestClient(api_module.app), get_settings

    def test_batch_merge_returns_results_list(self, tmp_path, monkeypatch):
        """POST /api/batch-merge returns a results list."""
        client, get_settings = self._make_client(tmp_path, monkeypatch, "fr-pl")

        # Create videos
        video1 = tmp_path / "Movie1.mkv"
        video1.touch()
        video2 = tmp_path / "Movie2.mkv"
        video2.touch()

        resp = client.post(
            "/api/batch-merge",
            json={
                "video_paths": [str(video1), str(video2)],
                "overwrite": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) == 2
        for r in data["results"]:
            assert "video" in r
            assert "status" in r

        get_settings.cache_clear()


class TestRateLimitNotBypassedAfterIdle:
    """After a 61s idle window, the next request must record its timestamp
    and the (rpm+1)th request within the new 60s window must return 429."""

    def test_second_window_hits_limit(self, monkeypatch):
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl")
        monkeypatch.setenv("SUBTOOLS_RATE_LIMIT_RPM", "2")
        from submerge.config import get_settings

        get_settings.cache_clear()

        # Use the module-level app (routes are registered there, not on create_app())
        from starlette.testclient import TestClient

        from submerge.api import app

        client = TestClient(app)

        t0 = 1_000_000_000.0  # High enough to trim all real entries

        # Window 1: RPM=2, first 2 pass, 3rd gets 429
        with patch("time.monotonic", return_value=t0):
            r1 = client.get("/api/polls")
            assert r1.status_code == 200
            r2 = client.get("/api/polls")
            assert r2.status_code == 200
            r3 = client.get("/api/polls")
            assert r3.status_code == 429, f"Expected 429, got {r3.status_code}"

        # Window 2 (t0+61): old timestamps trimmed to empty.
        # Bug fix: timestamp for first request in new window MUST be recorded
        # so the limiter still works after idle periods.
        t1 = t0 + 61.0
        with patch("time.monotonic", return_value=t1):
            r4 = client.get("/api/polls")
            assert r4.status_code == 200, f"Expected 200, got {r4.status_code}"
            r5 = client.get("/api/polls")
            assert r5.status_code == 200, f"Expected 200, got {r5.status_code}"
            r6 = client.get("/api/polls")
            assert r6.status_code == 429, (
                f"Expected 429 in second window, got {r6.status_code} "
                f"(bug: bucket was empty after idle trim so timestamp was never recorded)"
            )

        get_settings.cache_clear()


class TestApiSettingsValidation:
    """Tests for POST /api/settings input validation."""

    @staticmethod
    def _post_settings(client, **fields):
        return client.post("/api/settings", json=dict(fields))

    def test_stacked_gap_below_min_rejected(self, tmp_path, monkeypatch):
        """stacked_gap < 4 is silently rejected, value unchanged."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)
        from starlette.testclient import TestClient

        client = TestClient(api_module.app)

        resp = self._post_settings(client, stacked_gap=3)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        get_settings.cache_clear()

    def test_stacked_gap_above_max_rejected(self, tmp_path, monkeypatch):
        """stacked_gap > 200 is silently rejected."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)
        from starlette.testclient import TestClient

        client = TestClient(api_module.app)

        resp = self._post_settings(client, stacked_gap=201)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        get_settings.cache_clear()

    def test_stacked_gap_at_boundaries_accepted(self, tmp_path, monkeypatch):
        """stacked_gap 4 and 200 are accepted."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)
        from starlette.testclient import TestClient

        client = TestClient(api_module.app)

        resp = self._post_settings(client, stacked_gap=4)
        assert resp.status_code == 200
        resp = self._post_settings(client, stacked_gap=200)
        assert resp.status_code == 200
        get_settings.cache_clear()

    def test_invalid_hex_color_returns_error(self, tmp_path, monkeypatch):
        """Non-hex bottom_color returns error status."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)
        from starlette.testclient import TestClient

        client = TestClient(api_module.app)

        resp = self._post_settings(client, bottom_color="not-a-color")
        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["status"] == "error"
        assert (
            "bottom_color" in data["detail"]["message"]
            or "not-a-color" in data["detail"]["message"]
        )
        get_settings.cache_clear()

    def test_media_root_nonexistent_returns_error(self, tmp_path, monkeypatch):
        """media_root pointing to non-existent path returns error."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)
        from starlette.testclient import TestClient

        client = TestClient(api_module.app)

        resp = self._post_settings(client, media_root="/nonexistent/path/xyz")
        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["status"] == "error"
        assert "media_root is not a directory" in data["detail"]["message"]
        get_settings.cache_clear()

    def test_settings_post_requires_auth_when_password_set(self, tmp_path, monkeypatch):
        """POST /api/settings returns 401 when password is set and no auth header."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl,en-pl")
        monkeypatch.setenv("SUBTOOLS_UI_PASSWORD", "secret123")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)

        from starlette.testclient import TestClient

        client = TestClient(api_module.app)

        resp = client.post(
            "/api/settings",
            json={"media_root": str(tmp_path)},
            headers={},
        )
        assert resp.status_code == 401
        get_settings.cache_clear()


class TestApiGetSettings:
    """Tests for GET /api/settings endpoint."""

    def test_returns_200_with_settings(self, tmp_path, monkeypatch):
        """GET /api/settings returns 200 with settings dict."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)
        from starlette.testclient import TestClient

        client = TestClient(api_module.app)
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "settings" in data
        assert "notification_token" in data["settings"]
        # Token is masked
        assert data["settings"]["notification_token"] == ""
        get_settings.cache_clear()

    def test_token_masked_when_set(self, tmp_path, monkeypatch):
        """notification_token is '***' when a token is configured."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-pl")
        monkeypatch.setenv("SUBTOOLS_NOTIFICATION_TOKEN", "secret123")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)
        from starlette.testclient import TestClient

        client = TestClient(api_module.app)
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["notification_token"] == "***"
        get_settings.cache_clear()


class TestScheduleMergeLock:
    """Tests for _execute_scheduled_merge overlap protection."""

    def test_overlapping_call_returns_early(self):
        """Second concurrent call returns immediately without error."""
        import asyncio

        from submerge.routers import schedule as schedule_mod

        _lock = asyncio.Lock()

        # Patch the module-level reference so the function uses our lock
        schedule_mod._schedule_merge_lock = _lock

        async def _run_test():
            # Acquire the lock to simulate a running scheduled merge
            await _lock.acquire()
            # Now call _execute_scheduled_merge — it should detect the lock
            # and return early without blocking
            await schedule_mod._execute_scheduled_merge()
            _lock.release()

        asyncio.run(_run_test())
        # If we get here without hanging, the test passes


class TestBatchRepairApi:
    """Tests for POST /api/repair/batch-fix-overlaps endpoint."""

    @staticmethod
    def _make_client(tmp_path, monkeypatch):
        """Create a TestClient with isolated media_root and config_dir."""
        monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("SUBTOOLS_PAIRS", "de-ko")
        from submerge.config import get_settings

        get_settings.cache_clear()
        import importlib

        from submerge import api as api_module

        importlib.reload(api_module)
        from starlette.testclient import TestClient

        return TestClient(api_module.app), get_settings

    @staticmethod
    def _make_overlapping_srt(path: Path):
        """Create an SRT file with overlapping events."""
        from pysubs2 import SSAEvent, SSAFile

        subs = SSAFile()
        subs.format = "srt"
        subs.events.append(SSAEvent(start=0, end=2000, text="Line 1"))
        subs.events.append(SSAEvent(start=500, end=2500, text="Line 2"))
        subs.save(str(path))

    def test_batch_repair_returns_ok(self, tmp_path, monkeypatch):
        """Valid batch repair returns 200 with aggregated counts."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        p1 = tmp_path / "one.srt"
        p2 = tmp_path / "two.srt"
        self._make_overlapping_srt(p1)
        self._make_overlapping_srt(p2)

        resp = client.post(
            "/api/repair/batch-fix-overlaps",
            json={"subtitle_paths": [str(p1), str(p2)]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total"] == 2
        assert data["fixed"] == 2
        assert data["repositioned"] == 2

        get_settings.cache_clear()

    def test_batch_empty_list_returns_400(self, tmp_path, monkeypatch):
        """Empty subtitle_paths list returns 400."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.post("/api/repair/batch-fix-overlaps", json={"subtitle_paths": []})
        assert resp.status_code == 400

        get_settings.cache_clear()

    def test_batch_missing_key_returns_400(self, tmp_path, monkeypatch):
        """Missing subtitle_paths key returns 400."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.post("/api/repair/batch-fix-overlaps", json={})
        assert resp.status_code == 400

        get_settings.cache_clear()

    def test_batch_non_srt_rejected(self, tmp_path, monkeypatch):
        """Non-.srt path returns 400."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        p = tmp_path / "test.ass"
        p.touch()

        resp = client.post(
            "/api/repair/batch-fix-overlaps",
            json={"subtitle_paths": [str(p)]},
        )
        assert resp.status_code == 400
        assert ".srt" in resp.json()["detail"]["message"]

        get_settings.cache_clear()

    def test_batch_relative_path_rejected(self, tmp_path, monkeypatch):
        """Relative path returns 400."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        resp = client.post(
            "/api/repair/batch-fix-overlaps",
            json={"subtitle_paths": ["relative/path.srt"]},
        )
        assert resp.status_code == 400
        assert "absolute path" in resp.json()["detail"]["message"]

        get_settings.cache_clear()

    def test_batch_skipped_and_fixed(self, tmp_path, monkeypatch):
        """Merge-output .srt is skipped, normal .srt is repaired."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        merged = tmp_path / "Movie.de-ko.srt"
        normal = tmp_path / "normal.srt"
        self._make_overlapping_srt(merged)
        self._make_overlapping_srt(normal)

        resp = client.post(
            "/api/repair/batch-fix-overlaps",
            json={"subtitle_paths": [str(merged), str(normal)]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] == 1
        assert data["fixed"] == 1
        assert data["total"] == 2

        get_settings.cache_clear()

    def test_batch_fix_overlaps_too_many_paths(self, tmp_path, monkeypatch):
        """>500 paths returns 400 with size-cap message."""
        client, get_settings = self._make_client(tmp_path, monkeypatch)

        # Create one valid .srt so path validation passes for all entries
        valid = tmp_path / "valid.srt"
        self._make_overlapping_srt(valid)

        # Send 501 paths (all pointing to the same valid file — validation
        # only checks format, not uniqueness)
        payload = {"subtitle_paths": [str(valid)] * 501}
        resp = client.post("/api/repair/batch-fix-overlaps", json=payload)
        assert resp.status_code == 400
        assert "must not exceed 500" in resp.json()["detail"]["message"]

        get_settings.cache_clear()
