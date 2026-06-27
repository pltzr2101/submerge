"""Bilingual subtitle merge into ASS file."""

from __future__ import annotations

import bisect
import copy
import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pysubs2
from pysubs2 import Alignment, Color, SSAFile, SSAStyle

logger = logging.getLogger(__name__)


class InvalidSubtitleError(Exception):
    """Invalid or unparseable subtitle file."""


@dataclass
class QualityWarning:
    """Warning about potential quality issues in merged output."""

    code: str
    message: str
    severity: Literal["warning", "error"]


@dataclass
class MergeConfig:
    """Configuration for bilingual merge.

    All style properties have per-language variants (``*_bottom``,
    ``*_top``) for language-specific sizing and styling.
    """

    color_bottom: str = "#FFFFFF"  # White
    color_top: str = "#FFFF00"  # Yellow
    fontsize_bottom: int = 22
    fontsize_top: int = 22
    font_bottom: str = ""
    font_top: str = ""
    bold_bottom: bool = False
    bold_top: bool = False
    outline_bottom: float = 2.0
    outline_top: float = 2.0
    outline_color_bottom: str = "#000000"
    outline_color_top: str = "#000000"
    shadow_bottom: float = 1.0
    shadow_top: float = 1.0
    margin_v_bottom: float = 20
    margin_v_top: float = 20
    margin_h_bottom: float = 20
    margin_h_top: float = 20
    spacing_bottom: float = 0.0
    spacing_top: float = 0.0
    stacked_gap: int = 40
    layout: Literal["top-bottom", "stacked"] = "top-bottom"
    _fingerprint: str = ""  # Set by process_bilingual_merge, embedded in .ass


def _hex_to_color(hex_color: str) -> Color:
    """Convert hex color (#RRGGBB) to pysubs2 Color.

    Note: pysubs2 Color uses BGR format with alpha.
    """
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        raise ValueError(f"Invalid color format: #{hex_color}. Expected: #RRGGBB")

    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    # pysubs2 Color: (r, g, b, a) where a=0 means opaque
    return Color(r, g, b, 0)


def run_quality_checks(
    merged: SSAFile,
    sub1_name: str,
    sub2_name: str,
) -> list[QualityWarning]:
    """Run post-merge quality checks on the merged ASS file.

    Performs four independent checks:
    - OVERLAP_BOTTOM: overlapping bottom-track events (possible duplicate language source)
    - SUSPICIOUS_RATIO: extreme imbalance between bottom/top event counts
    - LOW_COVERAGE: many bottom events have no temporal match in top track
    - EMPTY_TRACK: one track contributed zero events

    Args:
        merged: The merged SSAFile after deduplication.
        sub1_name: Filename of the first (bottom) subtitle source.
        sub2_name: Filename of the second (top) subtitle source.

    Returns:
        List of QualityWarning objects (may be empty if no issues found).
    """
    warnings: list[QualityWarning] = []

    bottom_events = [e for e in merged.events if e.style == "bottom"]
    top_events = [e for e in merged.events if e.style == "top"]

    # --- Check D: EMPTY_TRACK ---
    empty_found = False
    if len(bottom_events) == 0:
        warnings.append(
            QualityWarning(
                code="EMPTY_TRACK",
                severity="error",
                message=f"Track '{sub1_name}' contributed 0 events to merged output",
            )
        )
        empty_found = True
    if len(top_events) == 0:
        warnings.append(
            QualityWarning(
                code="EMPTY_TRACK",
                severity="error",
                message=f"Track '{sub2_name}' contributed 0 events to merged output",
            )
        )
        empty_found = True
    if empty_found:
        return warnings  # ratio/coverage meaningless with empty track

    # --- Check A: OVERLAP_BOTTOM ---
    bottom_sorted = sorted(bottom_events, key=lambda e: e.start)
    overlap_count = 0
    for i in range(len(bottom_sorted) - 1):
        if bottom_sorted[i].end > bottom_sorted[i + 1].start:
            overlap_count += 1
    if overlap_count >= 3:
        warnings.append(
            QualityWarning(
                code="OVERLAP_BOTTOM",
                severity="warning",
                message=(
                    f"Found {overlap_count} overlapping bottom-track events "
                    f"— possible duplicate language source track"
                ),
            )
        )

    # --- Check B: SUSPICIOUS_RATIO ---
    ratio = len(bottom_events) / max(len(top_events), 1)
    if ratio > 3.0 or ratio < 0.33:
        warnings.append(
            QualityWarning(
                code="SUSPICIOUS_RATIO",
                severity="warning",
                message=(
                    f"Line count imbalance: bottom={len(bottom_events)}, "
                    f"top={len(top_events)}, ratio={ratio:.1f}"
                ),
            )
        )

    # --- Check C: LOW_COVERAGE ---
    # Optimised with bisect: O((n+m) log m) instead of O(n*m).

    sorted_top = sorted(top_events, key=lambda e: e.start)
    top_starts = [te.start for te in sorted_top]
    covered = 0

    for be in bottom_events:
        # Find first top event whose start >= be.end — all earlier
        # events (indices < idx_end) start before be.end and are
        # therefore potential overlaps.
        idx_end = bisect.bisect_left(top_starts, be.end)
        found = False
        for i in range(idx_end - 1, -1, -1):
            if sorted_top[i].end > be.start:
                found = True
                break
        if found:
            covered += 1
    coverage = covered / max(len(bottom_events), 1)
    if coverage < 0.55:
        warnings.append(
            QualityWarning(
                code="LOW_COVERAGE",
                severity="warning",
                message=(f"Only {coverage:.0%} of bottom events have a matching top event"),
            )
        )

    return warnings


# Regex to strip inline alignment/position/move overrides from subtitle text.
# Tags like {\an8}, {\an2}, {\pos(100,200)}, {\move(...)} override the
# per-style alignment set by merge_bilingual and must be removed.
_ALIGNMENT_OVERRIDE_RE = re.compile(
    r"\{[^}]*\\(?:an\d|pos\([^)]*\)|move\([^)]*\))[^}]*\}",
    re.IGNORECASE,
)


def _clean_event(event, style_name: str, strip_newlines: bool = False):
    """Return a shallow copy of *event* with alignment/position overrides
    stripped. For bottom-style events, explicit ``\\N`` / ``\\n`` line
    breaks are also removed so the renderer (wrap_style=0) handles
    wrapping instead, preventing the block from growing upward and
    overlapping the top subtitle.
    """
    ev = copy.copy(event)
    text = _ALIGNMENT_OVERRIDE_RE.sub("", ev.text)
    if strip_newlines:
        # Replace hard line breaks (\\N) and soft line breaks (\\n) with a
        # space.  The ASS renderer with wrap_style=0 will re-wrap the text
        # as needed.
        text = re.sub(r"\\[Nn]", " ", text)
        text = re.sub(r"  +", " ", text).strip()
    ev.text = text
    ev.style = style_name
    return ev


def _deduplicate_bottom_by_top_coverage(
    events: list,
) -> tuple[list, int]:
    """Remove duplicate bottom-track events caused by two same-language sources.

    A bottom event is considered a duplicate when:
    1. Two or more bottom events overlap the same top event temporally, AND
    2. The bottom events also overlap each other mutually (mutual_overlap > 0 ms).

    Condition 2 prevents removal of legitimate two-liner cases where a single
    long top event is correctly covered by two sequential bottom events.

    For each group of mutual-overlapping bottom candidates per top event,
    the one with the greatest temporal overlap with the top event is kept;
    all others are marked for removal.

    Non-bottom events are never modified.

    Args:
        events: List of SSAEvent objects (mixed styles).

    Returns:
        Tuple of (cleaned event list, number of removed events).
    """
    top_events = [e for e in events if e.style == "top"]
    bottom_events = [e for e in events if e.style == "bottom"]
    other_events = [e for e in events if e.style not in ("top", "bottom")]

    to_remove: set[int] = set()  # indices into bottom_events

    for top in top_events:
        # Find all bottom events overlapping this top event
        candidates_idx = [
            i for i, b in enumerate(bottom_events) if b.start < top.end and b.end > top.start
        ]
        if len(candidates_idx) <= 1:
            continue

        # Group candidates that mutually overlap each other
        # (this protects legitimate sequential 2-liners)
        groups: list[list[int]] = []
        used: set[int] = set()
        for i, idx_a in enumerate(candidates_idx):
            if idx_a in used:
                continue
            group = [idx_a]
            used.add(idx_a)
            a = bottom_events[idx_a]
            for idx_b in candidates_idx[i + 1 :]:
                if idx_b in used:
                    continue
                b = bottom_events[idx_b]
                mutual = min(a.end, b.end) - max(a.start, b.start)
                if mutual > 0:
                    group.append(idx_b)
                    used.add(idx_b)
            if len(group) > 1:
                groups.append(group)

        # Per group: keep the one with the largest overlap with top, remove rest
        for group in groups:
            best_idx = max(
                group,
                key=lambda i: min(bottom_events[i].end, top.end)
                - max(bottom_events[i].start, top.start),
            )
            for idx in group:
                if idx != best_idx:
                    to_remove.add(idx)

    kept_bottom = [b for i, b in enumerate(bottom_events) if i not in to_remove]
    result = sorted(
        other_events + top_events + kept_bottom,
        key=lambda e: e.start,
    )
    return result, len(to_remove)


def deduplicate_near_dupes(events: list) -> list:
    """Remove near-duplicate bottom events from two same-language source tracks.

    Two bottom events are near-duplicates when they:
    1. Overlap each other by at least 200 ms, AND
    2. Have identical text or are semantically similar
       (``difflib.SequenceMatcher.ratio() >= 0.85``).

    When a pair is detected the event that has the greater temporal overlap
    with the best-matching top event is kept; ties are broken in favour of
    the earlier event (``b1``).  Non-bottom events are never modified.

    Args:
        events: List of SSAEvent objects (mixed styles).

    Returns:
        Filtered event list sorted by start time.
    """
    top_events = [e for e in events if e.style == "top"]
    bottom_events = [e for e in events if e.style == "bottom"]
    other_events = [e for e in events if e.style not in ("top", "bottom")]

    bottom_events.sort(key=lambda e: e.start)
    to_remove: set[int] = set()

    for i in range(len(bottom_events)):
        if i in to_remove:
            continue
        b1 = bottom_events[i]
        for j in range(i + 1, len(bottom_events)):
            if j in to_remove:
                continue
            b2 = bottom_events[j]

            # Condition 1: at least 200 ms mutual overlap
            overlap = min(b1.end, b2.end) - max(b1.start, b2.start)
            if overlap < 200:
                continue

            # Condition 2: identical or semantically similar text
            text1 = b1.text.strip()
            text2 = b2.text.strip()
            if text1 != text2:
                ratio = difflib.SequenceMatcher(None, text1, text2).ratio()
                if ratio < 0.85:
                    continue

            # Tiebreaker: larger overlap with best-matching top event
            overlap1 = _best_top_overlap(b1, top_events)
            overlap2 = _best_top_overlap(b2, top_events)

            if overlap1 >= overlap2:
                to_remove.add(j)
            else:
                to_remove.add(i)
                break  # b1 removed — skip remaining comparisons for this b1

    kept_bottom = [b for i, b in enumerate(bottom_events) if i not in to_remove]
    return sorted(other_events + top_events + kept_bottom, key=lambda e: e.start)


def _best_top_overlap(bottom_event, top_events: list) -> int:
    """Return the maximum temporal overlap (ms) with any top event."""
    best = 0
    b_start, b_end = bottom_event.start, bottom_event.end
    for t in top_events:
        overlap = min(b_end, t.end) - max(b_start, t.start)
        if overlap > best:
            best = overlap
    return best


def _load_subtitle_file(path: Path) -> SSAFile:
    """Load a subtitle file with encoding handling."""
    try:
        # pysubs2 handles encoding detection automatically
        return pysubs2.load(str(path), encoding="utf-8")
    except UnicodeDecodeError:
        # Fallback: use charset_normalizer for robust auto-detection
        logger.warning(f"UTF-8 encoding failed for {path.name}, auto-detecting...")
        try:
            from charset_normalizer import from_path as _detect

            result = _detect(path).best()
            if result is not None:
                content = str(result)
                logger.info(f"Detected encoding for {path.name}: {result.encoding}")
                return pysubs2.SSAFile.from_string(content)
            # charset_normalizer couldn't determine encoding → try EUC-KR/CP949 as
            # last resort (common for Korean subtitle files from Asian sources)
            logger.warning(f"Auto-detection failed for {path.name}, trying EUC-KR fallback...")
            for fallback_enc in ("euc-kr", "cp949", "latin-1"):
                try:
                    content = path.read_bytes().decode(fallback_enc, errors="replace")
                    subs = pysubs2.SSAFile.from_string(content)
                    logger.warning(
                        f"Loaded {path.name} with fallback encoding {fallback_enc} "
                        f"(may contain replacement chars)"
                    )
                    return subs
                except Exception:
                    continue
            raise InvalidSubtitleError(f"Could not detect encoding for {path.name}")
        except InvalidSubtitleError:
            raise
        except Exception as e:
            raise InvalidSubtitleError(f"Failed to load {path.name}: {e}") from e
    except Exception as e:
        raise InvalidSubtitleError(f"Parsing error {path.name}: {e}") from e


def merge_bilingual(
    sub1_path: str | Path,
    sub2_path: str | Path,
    output_path: str | Path,
    config: MergeConfig | None = None,
) -> tuple[Path, list[QualityWarning]]:
    """Merge two subtitle files into a bilingual ASS file.

    Args:
        sub1_path: Path to first file (displayed at bottom)
        sub2_path: Path to second file (displayed at top)
        output_path: Output path for ASS file
        config: Style configuration (optional)

    Returns:
        Tuple of (output path, list of quality warnings)

    Raises:
        InvalidSubtitleError: If a file cannot be loaded
    """
    if config is None:
        config = MergeConfig()

    sub1_path = Path(sub1_path)
    sub2_path = Path(sub2_path)
    output_path = Path(output_path)

    # Validate that files exist
    for path in [sub1_path, sub2_path]:
        if not path.exists():
            raise InvalidSubtitleError(f"File not found: {path}")

    # Load files
    subs1 = _load_subtitle_file(sub1_path)
    subs2 = _load_subtitle_file(sub2_path)

    logger.info(f"Loaded {sub1_path.name}: {len(subs1)} lines")
    logger.info(f"Loaded {sub2_path.name}: {len(subs2)} lines")

    # Filter corrupt events where end <= start (zero/negative duration)
    for path, subs in ((sub1_path, subs1), (sub2_path, subs2)):
        for e in list(subs):
            if e.end <= e.start:
                logger.warning(
                    f"Skipped corrupt event in {path.name}: "
                    f"start={e.start} >= end={e.end}, text={e.text!r}"
                )
                subs.remove(e)
    if len(subs1) == 0 and len(subs2) == 0:
        raise InvalidSubtitleError("All events in both subtitle files are corrupt (end <= start)")

    # Create output file
    merged = SSAFile()

    # Resolve fonts and style parameters
    font_bottom = config.font_bottom
    font_top = config.font_top
    shadow_bottom = config.shadow_bottom
    shadow_top = config.shadow_top
    fontsize_bottom = config.fontsize_bottom
    fontsize_top = config.fontsize_top
    outline_bottom = config.outline_bottom
    outline_top = config.outline_top

    bold_bottom = -1 if config.bold_bottom else 0
    bold_top = -1 if config.bold_top else 0

    # Define styles based on layout
    if config.layout == "stacked":
        # Both at bottom, one above the other
        margin_top_calc = config.margin_v_bottom + config.stacked_gap

        merged.styles["bottom"] = SSAStyle(
            fontname=font_bottom,
            fontsize=fontsize_bottom,
            bold=bold_bottom,
            primarycolor=_hex_to_color(config.color_bottom),
            outlinecolor=_hex_to_color(config.outline_color_bottom),
            alignment=Alignment.BOTTOM_CENTER,
            marginv=config.margin_v_bottom,
            marginl=config.margin_h_bottom,
            marginr=config.margin_h_bottom,
            outline=outline_bottom,
            shadow=shadow_bottom,
            spacing=config.spacing_bottom,
        )
        merged.styles["top"] = SSAStyle(
            fontname=font_top,
            fontsize=fontsize_top,
            bold=bold_top,
            primarycolor=_hex_to_color(config.color_top),
            outlinecolor=_hex_to_color(config.outline_color_top),
            alignment=Alignment.BOTTOM_CENTER,
            marginv=margin_top_calc,
            marginl=config.margin_h_top,
            marginr=config.margin_h_top,
            outline=outline_top,
            shadow=shadow_top,
            spacing=config.spacing_top,
        )
    else:
        # top-bottom (default): one at top, one at bottom
        merged.styles["bottom"] = SSAStyle(
            fontname=font_bottom,
            fontsize=fontsize_bottom,
            bold=bold_bottom,
            primarycolor=_hex_to_color(config.color_bottom),
            outlinecolor=_hex_to_color(config.outline_color_bottom),
            alignment=Alignment.BOTTOM_CENTER,
            marginv=config.margin_v_bottom,
            marginl=config.margin_h_bottom,
            marginr=config.margin_h_bottom,
            outline=outline_bottom,
            shadow=shadow_bottom,
            spacing=config.spacing_bottom,
        )
        merged.styles["top"] = SSAStyle(
            fontname=font_top,
            fontsize=fontsize_top,
            bold=bold_top,
            primarycolor=_hex_to_color(config.color_top),
            outlinecolor=_hex_to_color(config.outline_color_top),
            alignment=Alignment.TOP_CENTER,
            marginv=config.margin_v_top,
            marginl=config.margin_h_top,
            marginr=config.margin_h_top,
            outline=outline_top,
            shadow=shadow_top,
            spacing=config.spacing_top,
        )

    # Enable smart line breaking for CJK text (Korean, Chinese, Japanese)
    for style_name in ("bottom", "top"):
        merged.styles[style_name].wrap_style = 0  # SMART_RT

    # Add events with their styles, stripping alignment/position overrides.
    # Bottom events also have explicit \N / \n line breaks removed so the
    # renderer handles wrapping — otherwise a multi-line block grows upward
    # and overlaps the top subtitle.
    for event in subs1:
        merged.append(_clean_event(event, "bottom", strip_newlines=True))

    for event in subs2:
        merged.append(_clean_event(event, "top", strip_newlines=False))

    # Sort by start time, with top-style events preceding bottom-style
    # events at the same timestamp (ensures consistent rendering order)
    merged.events.sort(key=lambda e: (e.start, 0 if e.style == "top" else 1))

    # Deduplicate events with identical (start, end, style, text) —
    # can happen when the same language track appears in both a normal
    # and an SDH/HI variant that was accidentally merged.
    seen: set[tuple[int, int, str, str]] = set()
    unique_events: list = []
    for event in merged.events:
        key = (event.start, event.end, event.style, event.plaintext)
        if key in seen:
            logger.warning(
                f"Deduplicated duplicate event: "
                f"start={event.start}, end={event.end}, "
                f"style={event.style}, text={event.plaintext!r}"
            )
        else:
            seen.add(key)
            unique_events.append(event)
    removed = len(merged.events) - len(unique_events)
    if removed:
        logger.info(f"Deduplication removed {removed} duplicate event(s)")
    merged.events = unique_events

    # Deduplicate near-duplicate bottom events (same timing, similar text)
    merged.events = deduplicate_near_dupes(merged.events)

    # Deduplicate bottom events caused by two same-language source tracks
    merged.events, near_dup_removed = _deduplicate_bottom_by_top_coverage(merged.events)
    if near_dup_removed:
        logger.info(
            f"Bottom-dedup removed {near_dup_removed} duplicate bottom event(s) "
            f"(same-language source detected)"
        )

    # Run post-merge quality checks
    sub1_name = sub1_path.name
    sub2_name = sub2_path.name
    warnings = run_quality_checks(merged, sub1_name, sub2_name)
    if warnings:
        for w in warnings:
            logger.warning(f"Quality check [{w.code}] {w.message}")

    # Save as ASS
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Embed config fingerprint for re-merge detection
    if config._fingerprint:
        merged.info["SubmergeConfigHash"] = config._fingerprint
    merged.save(str(output_path))

    logger.info(
        f"Bilingual file created: {output_path} ({len(subs1)} + {len(subs2)} = {len(merged)} lines)"
    )

    return output_path, warnings
