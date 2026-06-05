"""Tests for the notify module — sync and async notification sending."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from submerge.config import get_settings_for_test
from submerge.notify import _send_notification_sync, send_notification, send_notification_async


@pytest.fixture
def notify_settings():
    """Settings with notification_url configured."""
    return get_settings_for_test(
        pairs="de-ko",
        notification_url="https://ntfy.example.com/submerge",
        notification_token="test-token",
    )


@pytest.fixture
def notify_settings_noop():
    """Settings without notification_url — all calls should be no-op."""
    return get_settings_for_test(pairs="de-ko")


class TestSendNotificationSync:
    """Tests for synchronous send_notification."""

    def test_noop_when_url_empty(self, notify_settings_noop):
        """No HTTP call when notification_url is empty."""
        with patch("submerge.notify.httpx.Client") as mock_client:
            send_notification("test", "msg", notify_settings_noop)
        mock_client.assert_not_called()

    def test_posts_with_correct_headers(self, notify_settings):
        """POSTs to the configured URL with title, tags, and auth."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_client.__enter__.return_value.post.return_value = mock_resp

        with patch("submerge.notify.httpx.Client", return_value=mock_client):
            send_notification("Title", "Body text", notify_settings, tags=["tag1", "tag2"])

        mock_client.__enter__.return_value.post.assert_called_once()
        call_kwargs = mock_client.__enter__.return_value.post.call_args
        assert call_kwargs[0][0] == "https://ntfy.example.com/submerge"
        assert call_kwargs[1]["content"] == b"Body text"
        headers = call_kwargs[1]["headers"]
        assert headers["Title"] == "Title"
        assert headers["Authorization"] == "Bearer test-token"
        assert headers["Tags"] == "tag1,tag2"

    def test_logs_warning_on_http_error(self, notify_settings, caplog):
        """Logs a warning (not an exception) when the HTTP call fails."""
        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.side_effect = ConnectionError("refused")

        with patch("submerge.notify.httpx.Client", return_value=mock_client):
            send_notification("test", "msg", notify_settings)

        assert "Notification failed" in caplog.text

    def test_send_without_token(self, notify_settings):
        """Omits Authorization header when token is empty."""
        settings = get_settings_for_test(
            pairs="de-ko",
            notification_url="https://ntfy.example.com/submerge",
        )
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_client.__enter__.return_value.post.return_value = mock_resp

        with patch("submerge.notify.httpx.Client", return_value=mock_client):
            send_notification("T", "M", settings)

        headers = mock_client.__enter__.return_value.post.call_args[1]["headers"]
        assert "Authorization" not in headers


class TestSendNotificationAsync:
    """Tests for async send_notification_async."""

    @pytest.mark.asyncio
    async def test_noop_when_url_empty(self, notify_settings_noop):
        """No HTTP call when notification_url is empty."""
        with patch("submerge.notify.httpx.AsyncClient") as mock_client:
            await send_notification_async("test", "msg", notify_settings_noop)
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_posts_with_correct_headers(self, notify_settings):
        """POSTs asynchronously to the configured URL."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None

        called_headers: dict[str, str] = {}

        async def _fake_post(url, **kwargs):
            called_headers.update(kwargs.get("headers", {}))
            return mock_resp

        mock_client.__aenter__.return_value.post = _fake_post

        with patch("submerge.notify.httpx.AsyncClient", return_value=mock_client):
            await send_notification_async("Title", "Body", notify_settings, tags=["x"])

        assert called_headers.get("Title") == "Title"
        assert called_headers.get("Tags") == "x"

    @pytest.mark.asyncio
    async def test_logs_warning_on_http_error(self, notify_settings, caplog):
        """Logs a warning when the async HTTP call fails."""
        mock_client = MagicMock()
        mock_client.__aenter__.return_value.post.side_effect = ConnectionError("refused")

        with patch("submerge.notify.httpx.AsyncClient", return_value=mock_client):
            await send_notification_async("test", "msg", notify_settings)

        assert "Notification failed" in caplog.text


class TestInternalSyncImpl:
    """Tests for _send_notification_sync (delegated from send_notification)."""

    def test_noop_when_url_empty(self, notify_settings_noop):
        """No call when url is empty."""
        assert _send_notification_sync("x", "y", notify_settings_noop) is None
