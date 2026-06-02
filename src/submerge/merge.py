"""Bilingual subtitle merge into ASS file."""

from __future__ import annotations

import copy
import logging
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pysubs2
from pysubs2 import Alignment, Color, SSAFile, SSAStyle

logger = logging.getLogger(__name__)


class InvalidSubtitleError(Exception):
    """Invalid or unparseable subtitle file."""


@dataclass
class MergeConfig:
    """Configuration for bilingual merge.

    Per-style fields (fontsize_bottom, fontsize_top, outline_bottom,
    outline_top) default to None, falling back to the generic ``fontsize``
    and ``outline`` values. Set per-style fields explicitly for
    language-specific sizing.
    """

    color_bottom: str = "#FFFFFF"  # White
    color_top: str = "#FFFF00"  # Yellow
    fontsize: int = 22
    fontsize_bottom: int | None = None  # None = inherit fontsize
    fontsize_top: int | None = None
    font_bottom: str = ""
    font_top: str = ""
    bold_bottom: bool = False
    bold_top: bool = False
    outline: float = 2.0
    outline_bottom: float | None = None  # None = inherit outline
    outline_top: float | None = None
    outline_color_bottom: str = "#000000"
    outline_color_top: str = "#000000"
    shadow: float = 1.0  # Enabled by default for readability
    shadow_bottom: float | None = None  # None = inherit shadow
    shadow_top: float | None = None
    margin_v_bottom: float = 20
    margin_v_top: float = 20
    margin_h_bottom: float = 20
    margin_h_top: float = 20
    spacing_bottom: float = 0.0
    spacing_top: float = 0.0
    stacked_gap: int = 40
    layout: Literal["top-bottom", "stacked"] = "top-bottom"


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


# Regex to strip inline alignment/position/move overrides from subtitle text.
# Tags like {\an8}, {\an2}, {\pos(100,200)}, {\move(...)} override the
# per-style alignment set by merge_bilingual and must be removed.
_ALIGNMENT_OVERRIDE_RE = re.compile(
    r"\{[^}]*\\(?:an\d|pos\([^)]*\)|move\([^)]*\))[^}]*\}",
    re.IGNORECASE,
)


def _clean_event(event, style_name: str):
    """Return a shallow copy of *event* with alignment/position overrides
    stripped from its text and *style_name* assigned."""
    ev = copy.copy(event)
    ev.text = _ALIGNMENT_OVERRIDE_RE.sub("", ev.text)
    ev.style = style_name
    return ev


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
            if result is None:
                raise InvalidSubtitleError(f"Could not detect encoding for {path.name}")
            content = str(result)
            subs = pysubs2.SSAFile.from_string(content)
            return subs
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
) -> Path:
    """Merge two subtitle files into a bilingual ASS file.

    Args:
        sub1_path: Path to first file (displayed at bottom)
        sub2_path: Path to second file (displayed at top)
        output_path: Output path for ASS file
        config: Style configuration (optional)

    Returns:
        Path to created ASS file

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

    # Resolve fonts, shadows, fontsize, outline
    font_bottom = config.font_bottom
    font_top = config.font_top
    shadow_bottom = config.shadow_bottom if config.shadow_bottom is not None else config.shadow
    shadow_top = config.shadow_top if config.shadow_top is not None else config.shadow
    if config.fontsize_bottom is None or config.fontsize_top is None:
        warnings.warn(
            "MergeConfig.fontsize is deprecated; set fontsize_bottom and fontsize_top explicitly.",
            DeprecationWarning,
            stacklevel=2,
        )
    fontsize_bottom = (
        config.fontsize_bottom if config.fontsize_bottom is not None else config.fontsize
    )
    fontsize_top = config.fontsize_top if config.fontsize_top is not None else config.fontsize
    if config.outline_bottom is None or config.outline_top is None:
        warnings.warn(
            "MergeConfig.outline is deprecated; use outline_bottom / outline_top",
            DeprecationWarning,
            stacklevel=2,
        )
    outline_bottom = config.outline_bottom if config.outline_bottom is not None else config.outline
    outline_top = config.outline_top if config.outline_top is not None else config.outline

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

    # Add events with their styles, stripping alignment/position overrides
    for event in subs1:
        merged.append(_clean_event(event, "bottom"))

    for event in subs2:
        merged.append(_clean_event(event, "top"))

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

    # Save as ASS
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(str(output_path))

    logger.info(
        f"Bilingual file created: {output_path} ({len(subs1)} + {len(subs2)} = {len(merged)} lines)"
    )

    return output_path
