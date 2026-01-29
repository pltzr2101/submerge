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

    def test_missing_pairs_raises_runtime_error(self, monkeypatch):
        """Server crashes if SUBTOOLS_PAIRS is not defined."""
        monkeypatch.delenv("SUBTOOLS_PAIRS", raising=False)
        from submerge.config import get_settings
        get_settings.cache_clear()

        import importlib
        import submerge.api

        with pytest.raises(RuntimeError, match="SUBTOOLS_PAIRS.*required"):
            importlib.reload(submerge.api)

        get_settings.cache_clear()


class TestValidatePath:
    """Tests for path validation."""

    def test_rejects_relative_path(self):
        """Rejects relative paths."""
        from submerge.api import validate_path
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            validate_path("relative/path.mkv", "video")

        assert exc_info.value.status_code == 400
        assert "absolute path" in str(exc_info.value.detail["message"])
