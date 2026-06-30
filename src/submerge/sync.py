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

    When *output_path* is None or equals *input_path*, the result
    overwrites *input_path* in-place.  A ``.bak`` backup is created
    before any modification in that case.

    When *output_path* is a different path, the synced result is
    written there and *input_path* is left untouched (no backup).

    The intermediate output file uses a ``.sync_tmp`` infix before
    the real extension (e.g. ``.sync_tmp.srt``) so that ffsubsync
    can detect the output format from the file extension.

    Args:
        reference_path: Path to reference file (well synchronized)
        input_path: Path to file to use as sync input
        output_path: Where to write the result (default: overwrite input in-place)

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

    # Resolve output path: None or same path means in-place
    in_place = output_path is None or Path(output_path).resolve() == input_path.resolve()
    target_path = input_path if in_place else Path(output_path)

    # Create parent directory for output if needed
    if not in_place:
        target_path.parent.mkdir(parents=True, exist_ok=True)

    # Validate input files
    for path in [reference_path, input_path]:
        if not path.exists():
            raise SyncError(f"File not found: {path}")
        _validate_subtitle_format(path)

    # Backup original only for in-place overwrites
    backup_path: Path | None = None
    if in_place:
        try:
            backup_path = input_path.with_name(input_path.name + ".bak")
            shutil.copy2(input_path, backup_path)
            logger.info(f"Backup created: {backup_path}")
        except OSError as e:
            raise SyncError(f"Failed to create backup: {e}") from e

    # Sync into a temporary file, then atomically replace the target
    # ffsubsync detects the output format from the file extension,
    # so the tmp file must carry the real subtitle suffix (e.g. .srt).
    tmp_output = target_path.with_name(target_path.stem + ".sync_tmp" + target_path.suffix)

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

    logger.debug(f"ffsubsync stdout:\n{result.stdout}")
    if result.stderr:
        logger.debug(f"ffsubsync stderr:\n{result.stderr}")

    if not tmp_output.exists():
        if in_place:
            backup_path.unlink(missing_ok=True)
        raise SyncError(f"Output file was not created: {tmp_output}")

    # Atomically move tmp to target (POSIX: rename is atomic)
    try:
        tmp_output.replace(target_path)
    except OSError as e:
        tmp_output.unlink(missing_ok=True)
        raise SyncError(
            f"Failed to atomically replace output file: {e}."
            + (f" Backup preserved at: {backup_path}" if in_place else "")
        ) from e
    logger.info(f"Subtitles written: {target_path}")

    # Parse offset from output (if available)
    offset_ms = _parse_offset(result.stdout + result.stderr)

    if offset_ms is not None and abs(offset_ms) > 30_000:
        logger.warning(f"Large offset detected: {offset_ms}ms — verify result")
        return SyncResult(success=False, output_path=target_path, offset_ms=offset_ms)

    if offset_ms is not None and abs(offset_ms) > 5000:
        logger.warning(
            f"Large sync offset detected: {offset_ms}ms. "
            "Verify that subtitles match the same media version."
        )

    logger.info(f"Subtitles synchronized: {target_path}")

    return SyncResult(success=True, output_path=target_path, offset_ms=offset_ms)


def sync_subtitles_to_video(
    video_path: str | Path,
    input_path: str | Path,
    output_path: str | Path | None = None,
) -> SyncResult:
    """Synchronize subtitles to a video's audio track.

    Uses ffsubsync audio analysis to detect speech moments
    and align subtitles. Slower than sub-to-sub but doesn't
    require reference subtitles.

    When *output_path* is None or equals *input_path*, the result
    overwrites *input_path* in-place.  A ``.bak`` backup is created
    before any modification in that case.

    When *output_path* is a different path, the synced result is
    written there and *input_path* is left untouched (no backup).

    The intermediate output file uses a ``.sync_tmp`` infix before
    the real extension (e.g. ``.sync_tmp.srt``) so that ffsubsync
    can detect the output format from the file extension.

    Args:
        video_path: Path to video file (MKV, MP4, etc.)
        input_path: Path to subtitle file to use as sync input
        output_path: Where to write the result (default: overwrite input in-place)

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

    # Resolve output path: None or same path means in-place
    in_place = output_path is None or Path(output_path).resolve() == input_path.resolve()
    target_path = input_path if in_place else Path(output_path)

    # Create parent directory for output if needed
    if not in_place:
        target_path.parent.mkdir(parents=True, exist_ok=True)

    # Validate input files
    if not video_path.exists():
        raise SyncError(f"Video file not found: {video_path}")
    if not input_path.exists():
        raise SyncError(f"File not found: {input_path}")
    _validate_subtitle_format(input_path)

    # Backup original only for in-place overwrites
    backup_path: Path | None = None
    if in_place:
        try:
            backup_path = input_path.with_name(input_path.name + ".bak")
            shutil.copy2(input_path, backup_path)
            logger.info(f"Backup created: {backup_path}")
        except OSError as e:
            raise SyncError(f"Failed to create backup: {e}") from e

    # Sync into a temporary file, then atomically replace the target
    # ffsubsync detects the output format from the file extension,
    # so the tmp file must carry the real subtitle suffix (e.g. .srt).
    tmp_output = target_path.with_name(target_path.stem + ".sync_tmp" + target_path.suffix)

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

    logger.debug(f"ffsubsync stdout:\n{result.stdout}")
    if result.stderr:
        logger.debug(f"ffsubsync stderr:\n{result.stderr}")

    if not tmp_output.exists():
        if in_place:
            backup_path.unlink(missing_ok=True)
        raise SyncError(f"Output file was not created: {tmp_output}")

    # Atomically move tmp to target (POSIX: rename is atomic)
    try:
        tmp_output.replace(target_path)
    except OSError as e:
        tmp_output.unlink(missing_ok=True)
        raise SyncError(
            f"Failed to atomically replace output file: {e}."
            + (f" Backup preserved at: {backup_path}" if in_place else "")
        ) from e
    logger.info(f"Subtitles written: {target_path}")

    # Parse offset from output (if available)
    offset_ms = _parse_offset(result.stdout + result.stderr)

    if offset_ms is not None and abs(offset_ms) > 30_000:
        logger.warning(f"Large offset detected: {offset_ms}ms — verify result")
        return SyncResult(success=False, output_path=target_path, offset_ms=offset_ms)

    if offset_ms is not None and abs(offset_ms) > 5000:
        logger.warning(
            f"Large sync offset detected: {offset_ms}ms. "
            "Verify that subtitles match the same media version."
        )

    logger.info(f"Subtitles synchronized: {target_path}")

    return SyncResult(success=True, output_path=target_path, offset_ms=offset_ms)


def _parse_offset(output: str) -> int | None:
    """Try to parse offset from ffsubsync output (returns milliseconds)."""
    # ffsubsync outputs seconds: "Detected offset: 1.234 seconds" or "Best offset: -2.1 s"
    match = re.search(
        r"(?:offset|shift)[:\s]+(-?\d+(?:\.\d+)?)\s*s(?:econds?)?",
        output,
        re.IGNORECASE,
    )
    if match:
        return int(float(match.group(1)) * 1000)
    return None
