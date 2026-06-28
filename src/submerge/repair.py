"""Single-track subtitle repair: fix overlapping events in-place.

This module is intentionally separate from merge.py to preserve the
single-responsibility boundary of the bilingual merge pipeline.
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path

import pysubs2
from pysubs2 import SSAFile

from .exceptions import InvalidSubtitleError
from .subtitle_io import _load_subtitle_file

logger = logging.getLogger(__name__)

# Regex patterns that match merge-output subtitle filenames of the form
# <stem>.<lang>-<lang>.<ext>, e.g. Movie.de-ko.srt or Episode.en-de.ass.
# These files are the product of bilingual merges and should normally not
# be re-processed by single-track repair.
MERGED_OUTPUT_PATTERNS: list[str] = [
    r"\.[a-z]{2,3}-[a-z]{2,3}\.(srt|ass|sub)$",
]


def fix_single_track_overlaps(subs: SSAFile) -> tuple[SSAFile, int]:
    """Resolve overlapping events in a single subtitle track.

    Strategy differs by subtitle format:
    - ASS/SSA: the later overlapping event receives an inline {\\an8}
      alignment override so it appears at the top of the screen while
      the earlier event stays at the bottom. Standard fansub practice
      for simultaneous dialogue.
    - SRT and all other formats: the later event's start time is nudged
      forward by 1 ms past the earlier event's end time, eliminating the
      overlap without injecting ASS-specific tags that SRT renderers
      would display as literal text.

    Only events where end > start are processed; corrupt events (end <=
    start) are left untouched. The function is idempotent: calling it
    twice on an already-fixed file produces no further changes.

    If format is None, SRT-safe nudging is used (no ASS tags injected).

    Args:
        subs: A loaded SSAFile (single track, any format supported by pysubs2).

    Returns:
        Tuple of (modified SSAFile, number_of_events_repositioned).
        If number_of_events_repositioned == 0, the file was not modified.
    """
    # Work on a deep copy so the caller's object is never mutated.
    result = copy.deepcopy(subs)
    is_ass = result.format in ("ass", "ssa") if result.format else False

    events = sorted(result.events, key=lambda e: e.start)
    repositioned = 0

    for i, ev in enumerate(events):
        if ev.end <= ev.start:
            continue
        for j in range(i + 1, len(events)):
            other = events[j]
            if other.start >= ev.end:
                break
            if other.end <= other.start:
                continue
            if is_ass:
                if not other.text.startswith(r"{\an8}"):
                    other.text = r"{\an8}" + other.text
                    repositioned += 1
            else:
                new_start = ev.end + 1
                if new_start < other.end:
                    other.start = new_start
                    repositioned += 1

    result.events = events
    return result, repositioned


def fix_overlaps_in_file(subtitle_path: Path) -> dict:
    """Load a subtitle file, fix overlapping events, and save it in-place.

    The file is only written to disk when at least one event was repositioned.
    If the file is already clean, it is not touched.

    Args:
        subtitle_path: Absolute path to the subtitle file. Must exist.

    Returns:
        Dict with keys:
            - "repositioned" (int): number of events that were fixed
            - "output_path" (str): absolute path of the (potentially modified) file
            - "modified" (bool): True if the file was written to disk

    Raises:
        InvalidSubtitleError: If the file cannot be loaded or parsed.
        FileNotFoundError: If subtitle_path does not exist.
    """
    if not subtitle_path.exists():
        raise FileNotFoundError(f"File not found: {subtitle_path}")

    try:
        subs = _load_subtitle_file(subtitle_path)
    except pysubs2.UnknownFileExtensionError as e:
        raise InvalidSubtitleError(f"Unsupported subtitle format: {subtitle_path.suffix}") from e

    fixed, count = fix_single_track_overlaps(subs)

    if count > 0:
        fixed.save(str(subtitle_path))
        logger.info(f"repair: {count} overlap(s) fixed in {subtitle_path.name}")
    else:
        logger.debug(f"repair: no overlaps in {subtitle_path.name}")

    return {
        "repositioned": count,
        "output_path": str(subtitle_path),
        "modified": count > 0,
    }


def repair_all_subtitles_in_root(
    media_root: Path,
    exclude_patterns: list[str] | None = None,
) -> dict:
    """Walk *media_root* and repair every ``.srt`` subtitle file in-place.

    Skips files that cannot be parsed (e.g. unknown extension, binary
    garbage) and logs a warning.  Only touches files that actually
    contain overlapping events.

    Merge-output files (e.g. ``Movie.de-ko.srt``) are skipped by default
    via :data:`MERGED_OUTPUT_PATTERNS`.  Pass a custom list of regex
    patterns to *exclude_patterns* to override this behaviour; pass an
    empty list to disable exclusion entirely.

    Args:
        media_root: Root directory to search recursively.
        exclude_patterns: Optional list of regex patterns (case-insensitive
            via ``re.search``).  Each pattern is tested against the
            lowercased filename.  When ``None`` (the default)
            :data:`MERGED_OUTPUT_PATTERNS` is used.

    Returns:
        Dict with ``fixed`` (number of files modified), ``total``
        (number of .srt files discovered) and ``skipped`` (number of
        .srt files excluded by *exclude_patterns*).
    """
    from pathlib import Path

    if exclude_patterns is None:
        exclude_patterns = MERGED_OUTPUT_PATTERNS

    patterns = [re.compile(pat, re.IGNORECASE) for pat in exclude_patterns]

    total = 0
    fixed = 0
    skipped = 0
    for srt_path in Path(media_root).rglob("*.srt"):
        total += 1

        # Check filename against exclude patterns (case-insensitive)
        name_lower = srt_path.name.lower()
        if any(pat.search(name_lower) for pat in patterns):
            logger.debug("repair: skipping merged-output file %s", srt_path.name)
            skipped += 1
            continue

        try:
            result = fix_overlaps_in_file(srt_path)
            if result["modified"]:
                fixed += 1
        except (InvalidSubtitleError, OSError) as e:
            logger.warning(
                "Skipping unrepairable subtitle %s: %s",
                srt_path,
                e,
            )
    logger.info(
        "repair-all: %d/%d .srt files repaired, %d skipped",
        fixed,
        total,
        skipped,
    )
    return {"fixed": fixed, "total": total, "skipped": skipped}
