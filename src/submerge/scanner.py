"""Media directory scanner for subtitle status overview."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SubtoolsSettings, get_settings
from .hook import find_subtitle_path

logger = logging.getLogger(__name__)

# Video file extensions to scan for
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".wmv", ".flv"}


@dataclass
class MediaEntry:
    """Represents one video entry with its subtitle status."""

    video_path: str
    video_name: str
    parent_dir: str  # Relative directory for grouping (e.g., "Show Name/Season 1")
    subtitle_status: dict[str, dict[str, Any]]
    # subtitle_status: {lang: {"present": bool, "path": str|None}}
    # merged_status: {pair: {"present": bool, "path": str|None}}


def _is_video_file(path: Path) -> bool:
    """Check if a path is a video file by extension."""
    return path.suffix.lower() in VIDEO_EXTENSIONS


def scan_directory(
    root_dir: str | Path,
    settings: SubtoolsSettings | None = None,
) -> list[MediaEntry]:
    """Scan a directory recursively for video files and subtitle status.

    Args:
        root_dir: Root directory to scan
        settings: Configuration for language pairs

    Returns:
        List of MediaEntry objects with subtitle status
    """
    settings = settings or get_settings()
    root = Path(root_dir).resolve()

    if not root.exists():
        logger.warning(f"Media root does not exist: {root}")
        return []

    entries: list[MediaEntry] = []

    for video_path in sorted(root.rglob("*")):
        if not video_path.is_file() or not _is_video_file(video_path):
            continue

        rel_path = video_path.relative_to(root)
        parent_dir = str(rel_path.parent) if str(rel_path.parent) != "." else "/"

        # Check subtitle status for each required language
        subtitle_status: dict[str, dict[str, Any]] = {}
        for lang in sorted(settings.required_langs):
            sub_path = find_subtitle_path(video_path, lang)
            subtitle_status[lang] = {
                "present": sub_path is not None,
                "path": str(sub_path) if sub_path else None,
            }

        # Check merged status for each pair
        merged_status: dict[str, dict[str, Any]] = {}
        all_merged = True
        for lang_bottom, lang_top in settings.pairs:
            pair_key = f"{lang_bottom}-{lang_top}"
            output_path = video_path.parent / f"{video_path.stem}.{pair_key}.ass"
            merged_status[pair_key] = {
                "present": output_path.exists(),
                "path": str(output_path) if output_path.exists() else None,
            }
            if not output_path.exists():
                all_merged = False

        # Determine overall status
        all_langs_present = all(s["present"] for s in subtitle_status.values())

        entry = MediaEntry(
            video_path=str(video_path),
            video_name=video_path.name,
            parent_dir=parent_dir,
            subtitle_status=subtitle_status,
        )
        # Attach merged status
        entry.merged_status = merged_status  # type: ignore[attr-defined]
        entry.all_langs_present = all_langs_present  # type: ignore[attr-defined]
        entry.all_merged = all_merged  # type: ignore[attr-defined]

        entries.append(entry)

    return entries


def find_videos_needing_merge(
    root_dir: str | Path,
    settings: SubtoolsSettings | None = None,
) -> list[MediaEntry]:
    """Find all videos that have all languages present but are not yet merged.

    Args:
        root_dir: Root directory to scan
        settings: Configuration

    Returns:
        List of MediaEntry for videos needing merge
    """
    entries = scan_directory(root_dir, settings)
    return [
        e for e in entries
        if e.all_langs_present and not e.all_merged  # type: ignore[attr-defined]
    ]


def entry_to_dict(entry: MediaEntry, settings: SubtoolsSettings | None = None) -> dict[str, Any]:
    """Convert a MediaEntry to a JSON-serializable dict."""
    settings = settings or get_settings()
    return {
        "video_path": entry.video_path,
        "video_name": entry.video_name,
        "parent_dir": entry.parent_dir,
        "subtitle_status": entry.subtitle_status,
        "merged_status": getattr(entry, "merged_status", {}),
        "all_langs_present": getattr(entry, "all_langs_present", False),
        "all_merged": getattr(entry, "all_merged", False),
        "pairs": [f"{b}-{t}" for b, t in settings.pairs],
        "required_langs": sorted(settings.required_langs),
    }
