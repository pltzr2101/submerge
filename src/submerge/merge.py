"""Bilingual subtitle merge into ASS file."""

from __future__ import annotations

import logging
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
    """Configuration for bilingual merge."""

    color_bottom: str = "#FFFFFF"  # White
    color_top: str = "#FFFF00"  # Yellow
    fontsize: int = 20
    font_name: str = "Roboto"
    font_bottom: str = ""  # Empty = inherit font_name
    font_top: str = ""  # Empty = inherit font_name
    bold_bottom: bool = False
    bold_top: bool = False
    outline: float = 2.0
    outline_color_bottom: str = "#000000"
    outline_color_top: str = "#000000"
    shadow: float = 0.0  # Disabled by default - cleaner look
    shadow_bottom: float | None = None  # None = inherit shadow
    shadow_top: float | None = None
    margin_v_bottom: int = 30
    margin_v_top: int = 15
    margin_h_bottom: int = 20
    margin_h_top: int = 20
    spacing_bottom: float = 0.0
    spacing_top: float = 0.0
    stacked_gap: int = 8
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

    # pysubs2 Color: (r, g, b, a) où a=0 signifie opaque
    return Color(r, g, b, 0)


def _load_subtitle_file(path: Path) -> SSAFile:
    """Load a subtitle file with encoding handling."""
    try:
        # pysubs2 handles encoding detection automatically
        return pysubs2.load(str(path), encoding="utf-8")
    except UnicodeDecodeError:
        # Fallback to automatic detection
        logger.warning(f"UTF-8 encoding failed for {path.name}, auto-detecting...")
        try:
            return pysubs2.load(str(path))
        except Exception as e:
            raise InvalidSubtitleError(
                f"Failed to load {path.name}: {e}"
            ) from e
    except Exception as e:
        raise InvalidSubtitleError(
            f"Parsing error {path.name}: {e}"
        ) from e


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

    # Create output file
    merged = SSAFile()

    # Resolve fonts and shadows
    font_bottom = config.font_bottom or config.font_name or "Arial"
    font_top = config.font_top or config.font_name or "Arial"
    shadow_bottom = config.shadow_bottom if config.shadow_bottom is not None else config.shadow
    shadow_top = config.shadow_top if config.shadow_top is not None else config.shadow

    bold_bottom = -1 if config.bold_bottom else 0
    bold_top = -1 if config.bold_top else 0

    # Define styles based on layout
    if config.layout == "stacked":
        # Both at bottom, one above the other
        margin_top_calc = config.margin_v_bottom + (config.stacked_gap or 8)

        merged.styles["bottom"] = SSAStyle(
            fontname=font_bottom,
            fontsize=config.fontsize,
            bold=bold_bottom,
            primarycolor=_hex_to_color(config.color_bottom),
            outlinecolor=_hex_to_color(config.outline_color_bottom),
            alignment=Alignment.BOTTOM_CENTER,
            marginv=config.margin_v_bottom,
            marginl=config.margin_h_bottom,
            marginr=config.margin_h_bottom,
            outline=config.outline,
            shadow=shadow_bottom,
            spacing=config.spacing_bottom,
        )
        merged.styles["top"] = SSAStyle(
            fontname=font_top,
            fontsize=config.fontsize,
            bold=bold_top,
            primarycolor=_hex_to_color(config.color_top),
            outlinecolor=_hex_to_color(config.outline_color_top),
            alignment=Alignment.BOTTOM_CENTER,
            marginv=margin_top_calc,
            marginl=config.margin_h_top,
            marginr=config.margin_h_top,
            outline=config.outline,
            shadow=shadow_top,
            spacing=config.spacing_top,
        )
    else:
        # top-bottom (default): one at top, one at bottom
        merged.styles["bottom"] = SSAStyle(
            fontname=font_bottom,
            fontsize=config.fontsize,
            bold=bold_bottom,
            primarycolor=_hex_to_color(config.color_bottom),
            outlinecolor=_hex_to_color(config.outline_color_bottom),
            alignment=Alignment.BOTTOM_CENTER,
            marginv=config.margin_v_bottom,
            marginl=config.margin_h_bottom,
            marginr=config.margin_h_bottom,
            outline=config.outline,
            shadow=shadow_bottom,
            spacing=config.spacing_bottom,
        )
        merged.styles["top"] = SSAStyle(
            fontname=font_top,
            fontsize=config.fontsize,
            bold=bold_top,
            primarycolor=_hex_to_color(config.color_top),
            outlinecolor=_hex_to_color(config.outline_color_top),
            alignment=Alignment.TOP_CENTER,
            marginv=config.margin_v_top,
            marginl=config.margin_h_top,
            marginr=config.margin_h_top,
            outline=config.outline,
            shadow=shadow_top,
            spacing=config.spacing_top,
        )

    # Add events with their styles
    for event in subs1:
        event.style = "bottom"
        merged.append(event)

    for event in subs2:
        event.style = "top"
        merged.append(event)

    # Sort by start time, with top-style events preceding bottom-style
    # events at the same timestamp (ensures consistent rendering order)
    merged.events.sort(key=lambda e: (e.start, 0 if e.style == "top" else 1))

    # Save as ASS
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(str(output_path))

    logger.info(
        f"Bilingual file created: {output_path} "
        f"({len(subs1)} + {len(subs2)} = {len(merged)} lines)"
    )

    return output_path
