"""Subtitle extraction from video files."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .probe import (
    SubtitleTrack,
    find_track_by_language,
    list_subtitle_tracks,
)

logger = logging.getLogger(__name__)


class SubtitleExtractionError(Exception):
    """Error during subtitle extraction."""


def extract_subtitles(
    video_path: str | Path,
    output_path: str | Path,
    track_index: int | None = None,
    language: str | None = None,
) -> Path:
    """Extract a subtitle track from a video file.

    Args:
        video_path: Path to video file
        output_path: Output path for subtitle file
        track_index: Track index to extract (takes priority over language)
        language: Language code of track to extract (en, fr, pl, etc.)

    Returns:
        Path to extracted file

    Raises:
        SubtitleExtractionError: If extraction fails
    """
    video_path = Path(video_path)
    output_path = Path(output_path)

    # Get available tracks
    tracks = list_subtitle_tracks(video_path)
    text_tracks = [t for t in tracks if t.is_text]

    # Determine which track to extract
    selected_track: SubtitleTrack | None = None

    if track_index is not None:
        # Search by index
        for track in text_tracks:
            if track.index == track_index:
                selected_track = track
                break
        if not selected_track:
            available = ", ".join(str(t.index) for t in text_tracks)
            raise SubtitleExtractionError(
                f"Track #{track_index} not found. Available tracks: {available}"
            )
    elif language:
        # Search by language
        selected_track = find_track_by_language(tracks, language)
        if not selected_track:
            available = ", ".join(t.language or "?" for t in text_tracks if t.language)
            raise SubtitleExtractionError(
                f"No '{language}' track found. Available languages: {available}"
            )
    else:
        # Default: first text track
        selected_track = text_tracks[0]

    logger.info(f"Extracting: {selected_track.display_name}")

    # Calculate relative index for -map 0:s:N
    # ffmpeg uses an index relative to subtitle tracks, not absolute index
    sub_relative_index = next(i for i, t in enumerate(tracks) if t.index == selected_track.index)

    cmd = [
        "ffmpeg",
        "-y",  # Overwrite
        "-i",
        str(video_path),
        "-map",
        f"0:s:{sub_relative_index}",
        "-c:s",
        "srt",  # Convertir en SRT
        str(output_path),
    ]

    logger.debug(f"Executing: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise SubtitleExtractionError("ffmpeg timeout - file may be corrupted") from None
    except subprocess.CalledProcessError as e:
        raise SubtitleExtractionError(f"ffmpeg failed: {e.stderr}") from e
    except FileNotFoundError:
        raise SubtitleExtractionError(
            "ffmpeg not found. Install ffmpeg: brew install ffmpeg"
        ) from None

    if not output_path.exists():
        raise SubtitleExtractionError(f"Output file was not created: {output_path}")

    # Check that file is not empty
    if output_path.stat().st_size == 0:
        raise SubtitleExtractionError(
            f"Extracted file is empty. Track #{selected_track.index} may be an unsupported format."
        )

    logger.info(f"Subtitles extracted: {output_path}")
    return output_path
