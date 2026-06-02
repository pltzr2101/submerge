"""Shared data types used by hook, queue, and API modules.

This module has NO imports from hook, queue, or api — it is a leaf module
that resolves the circular dependency between hook.py and queue.py.
"""

from __future__ import annotations

from dataclasses import dataclass


class HookError(Exception):
    """Base error for hook."""


class InvalidLanguageError(HookError):
    """Unsupported language."""


class ProcessingError(HookError):
    """Error during processing."""


@dataclass
class HookResult:
    """Result returned by process_hook."""

    status: str  # "merged", "waiting", "skipped", "already_processing", "polling"
    files: list[str] | None = None
    present: list[str] | None = None
    missing: list[str] | None = None
    reason: str | None = None


@dataclass
class QueueEntry:
    """A pending merge entry in the queue."""

    video_path: str
    langs_present: list[str]
    langs_missing: list[str]
    first_seen: str  # ISO datetime
    last_checked: str  # ISO datetime
    attempt_count: int
    status: str  # "pending", "done", "failed"
