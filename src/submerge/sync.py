"""Subtitle synchronization via ffsubsync."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Subtitle formats supported by ffsubsync
SUPPORTED_FORMATS = {".srt", ".ass", ".ssa", ".vtt"}


class SyncError(Exception):
    """Error during subtitle synchronization."""


class FfsubsyncNotFoundError(SyncError):
    """ffsubsync is not installed."""


@dataclass
class SyncResult:
    """Synchronization result."""

    success: bool
    output_path: Path
    offset_ms: int | None = None


def _get_ffsubsync_command() -> str:
    """Return ffsubsync path or raise an error."""
    ffs_path = shutil.which("ffs") or shutil.which("ffsubsync")
    if ffs_path is None:
        raise FfsubsyncNotFoundError("ffsubsync not found. Install it: uv add ffsubsync")
    return ffs_path


def _validate_subtitle_format(path: Path) -> None:
    """Validate that file is a supported subtitle format."""
    if path.suffix.lower() not in SUPPORTED_FORMATS:
        raise SyncError(
            f"Unsupported format: {path.suffix}. Supported formats: {', '.join(SUPPORTED_FORMATS)}"
        )


def sync_subtitles(
    reference_path: str | Path,
    input_path: str | Path,
    output_path: str | Path | None = None,
) -> SyncResult:
    """Synchronize subtitles to a reference via ffsubsync.

    The synced result replaces *input_path* in-place so that the
    file-name pattern ``{video_stem}.{lang}.srt`` is preserved —
    hook.py / scanner.py depend on this convention.

    A permanent ``.bak`` backup is created before any modification.

    Args:
        reference_path: Path to reference file (well synchronized)
        input_path: Path to file to synchronize (overwritten in-place)
        output_path: Ignored; kept for backwards compatibility only

    Returns:
        SyncResult with status, path to the synced file,
        and detected offset

    Raises:
        SyncError: If synchronization fails
        FfsubsyncNotFoundError: If ffsubsync is not installed
    """
    ffs_cmd = _get_ffsubsync_command()

    reference_path = Path(reference_path)
    input_path = Path(input_path)

    # Validate input files
    for path in [reference_path, input_path]:
        if not path.exists():
            raise SyncError(f"File not found: {path}")
        _validate_subtitle_format(path)

    # Backup original before any modification
    try:
        backup_path = input_path.with_name(input_path.name + ".bak")
        shutil.copy2(input_path, backup_path)
        logger.info(f"Backup created: {backup_path}")
    except OSError as e:
        raise SyncError(f"Failed to create backup: {e}") from e

    # Sync into a temporary file, then atomically replace the original
    tmp_output = input_path.with_name(input_path.name + ".tmp")

    cmd = [
        ffs_cmd,
        str(reference_path),
        "-i",
        str(input_path),
        "-o",
        str(tmp_output),
    ]

    logger.debug(f"Executing: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
    except subprocess.TimeoutExpired:
        tmp_output.unlink(missing_ok=True)
        raise SyncError("ffsubsync timeout - synchronization is taking too long") from None
    except subprocess.CalledProcessError as e:
        tmp_output.unlink(missing_ok=True)
        raise SyncError(f"ffsubsync failed:\n{e.stderr}") from e

    if not tmp_output.exists():
        raise SyncError(f"Output file was not created: {tmp_output}")

    # Atomically overwrite original (POSIX: rename is atomic)
    tmp_output.replace(input_path)
    logger.info(f"Original overwritten in-place: {input_path}")

    # Parse offset from output (if available)
    offset_ms = _parse_offset(result.stdout + result.stderr)

    if offset_ms is not None and abs(offset_ms) > 30_000:
        logger.warning(f"Large offset detected: {offset_ms}ms — verify result")
        return SyncResult(success=False, output_path=input_path, offset_ms=offset_ms)

    if offset_ms is not None and abs(offset_ms) > 5000:
        logger.warning(
            f"Large sync offset detected: {offset_ms}ms. "
            "Verify that subtitles match the same media version."
        )

    logger.info(f"Subtitles synchronized: {input_path}")

    return SyncResult(success=True, output_path=input_path, offset_ms=offset_ms)


def sync_subtitles_to_video(
    video_path: str | Path,
    input_path: str | Path,
    output_path: str | Path | None = None,
) -> SyncResult:
    """Synchronize subtitles to a video's audio track.

    Uses ffsubsync audio analysis to detect speech moments
    and align subtitles. Slower than sub-to-sub but doesn't
    require reference subtitles.

    The synced result replaces *input_path* in-place. A permanent
    ``.bak`` backup is created before any modification.

    Args:
        video_path: Path to video file (MKV, MP4, etc.)
        input_path: Path to subtitle file to synchronize (overwritten in-place)
        output_path: Ignored; kept for backwards compatibility only

    Returns:
        SyncResult with status, path to the synced file,
        and detected offset

    Raises:
        SyncError: If synchronization fails
        FfsubsyncNotFoundError: If ffsubsync is not installed
    """
    ffs_cmd = _get_ffsubsync_command()

    video_path = Path(video_path)
    input_path = Path(input_path)

    # Validate input files
    if not video_path.exists():
        raise SyncError(f"Video file not found: {video_path}")
    if not input_path.exists():
        raise SyncError(f"File not found: {input_path}")
    _validate_subtitle_format(input_path)

    # Backup original before any modification
    try:
        backup_path = input_path.with_name(input_path.name + ".bak")
        shutil.copy2(input_path, backup_path)
        logger.info(f"Backup created: {backup_path}")
    except OSError as e:
        raise SyncError(f"Failed to create backup: {e}") from e

    # Sync into a temporary file, then atomically replace the original
    tmp_output = input_path.with_name(input_path.name + ".tmp")

    cmd = [
        ffs_cmd,
        str(video_path),
        "-i",
        str(input_path),
        "-o",
        str(tmp_output),
    ]

    logger.debug(f"Executing: {' '.join(cmd)}")
    logger.info("Synchronizing to audio track (may take a few minutes)...")

    try:
        # Longer timeout for audio analysis
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=600)
    except subprocess.TimeoutExpired:
        tmp_output.unlink(missing_ok=True)
        raise SyncError("ffsubsync timeout - audio analysis is taking too long") from None
    except subprocess.CalledProcessError as e:
        tmp_output.unlink(missing_ok=True)
        raise SyncError(f"ffsubsync failed:\n{e.stderr}") from e

    if not tmp_output.exists():
        raise SyncError(f"Output file was not created: {tmp_output}")

    # Atomically overwrite original
    tmp_output.replace(input_path)
    logger.info(f"Original overwritten in-place: {input_path}")

    # Parse offset from output (if available)
    offset_ms = _parse_offset(result.stdout + result.stderr)

    if offset_ms is not None and abs(offset_ms) > 30_000:
        logger.warning(f"Large offset detected: {offset_ms}ms — verify result")
        return SyncResult(success=False, output_path=input_path, offset_ms=offset_ms)

    if offset_ms is not None and abs(offset_ms) > 5000:
        logger.warning(
            f"Large sync offset detected: {offset_ms}ms. "
            "Verify that subtitles match the same media version."
        )

    logger.info(f"Subtitles synchronized: {input_path}")

    return SyncResult(success=True, output_path=input_path, offset_ms=offset_ms)


def _parse_offset(output: str) -> int | None:
    """Try to parse offset from ffsubsync output."""
    # ffsubsync displays info about applied offset
    # Typical format: "offset: 1234ms" or similar
    match = re.search(r"offset[:\s]+(-?\d+)\s*ms", output, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Alternative format
    match = re.search(r"shift[:\s]+(-?\d+)\s*ms", output, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return None
