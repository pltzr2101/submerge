"""Outbound notification support (ntfy.sh / generic HTTP webhook)."""

from __future__ import annotations

import logging

import httpx2

from .config import SubtoolsSettings

logger = logging.getLogger(__name__)


def send_notification(
    title: str,
    message: str,
    settings: SubtoolsSettings,
    tags: list[str] | None = None,
) -> None:
    """POST a notification to settings.notification_url.

    Compatible with ntfy.sh (Title/Tags headers) and generic webhooks
    (JSON body). No-op if notification_url is empty.

    Security note: notification_url is passed directly to httpx without
    URL validation. This is intentional for self-hosted deployments where
    the operator controls the setting. Do not expose the settings UI to
    untrusted users.
    """
    url = (settings.notification_url or "").strip()
    if not url:
        return
    token = (settings.notification_token or "").strip()
    headers: dict[str, str] = {"Title": title}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if tags:
        headers["Tags"] = ",".join(tags)
    try:
        with httpx2.Client(timeout=10) as client:
            resp = client.post(url, content=message.encode(), headers=headers)
            resp.raise_for_status()
        logger.info(f"Notification sent: {title}")
    except Exception as e:
        logger.warning(f"Notification failed ({url}): {e}")
