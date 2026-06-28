"""Shared exception types for the submerge package."""
from __future__ import annotations


class InvalidSubtitleError(Exception):
    """Raised when a subtitle file cannot be loaded or parsed."""
