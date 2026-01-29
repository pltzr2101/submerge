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
    outline: float = 2.0
    shadow: float = 0.0  # Disabled by default - cleaner look
    layout: Literal["top-bottom", "stacked"] = "top-bottom"


def _calculate_margin_top(fontsize: int) -> int:
    """Calculate MarginV for the top subtitle in stacked mode."""
    return 10 + int(fontsize * 2.5)


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

    # Define styles based on layout
    if config.layout == "stacked":
        # Both at bottom, one above the other
        margin_top = _calculate_margin_top(config.fontsize)

        merged.styles["bottom"] = SSAStyle(
            fontname=config.font_name,
            fontsize=config.fontsize,
            primarycolor=_hex_to_color(config.color_bottom),
            alignment=Alignment.BOTTOM_CENTER,
            marginv=10,
            outline=config.outline,
            shadow=config.shadow,
        )
        merged.styles["top"] = SSAStyle(
            fontname=config.font_name,
            fontsize=config.fontsize,
            primarycolor=_hex_to_color(config.color_top),
            alignment=Alignment.BOTTOM_CENTER,
            marginv=margin_top,
            outline=config.outline,
            shadow=config.shadow,
        )
    else:
        # top-bottom (default): one at top, one at bottom
        merged.styles["bottom"] = SSAStyle(
            fontname=config.font_name,
            fontsize=config.fontsize,
            primarycolor=_hex_to_color(config.color_bottom),
            alignment=Alignment.BOTTOM_CENTER,
            outline=config.outline,
            shadow=config.shadow,
        )
        merged.styles["top"] = SSAStyle(
            fontname=config.font_name,
            fontsize=config.fontsize,
            primarycolor=_hex_to_color(config.color_top),
            alignment=Alignment.TOP_CENTER,
            outline=config.outline,
            shadow=config.shadow,
        )

    # Add events with their styles
    for event in subs1:
        event.style = "bottom"
        merged.append(event)

    for event in subs2:
        event.style = "top"
        merged.append(event)

    # Sort by start time
    merged.sort()

    # Save as ASS
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(str(output_path))

    logger.info(
        f"Bilingual file created: {output_path} "
        f"({len(subs1)} + {len(subs2)} = {len(merged)} lines)"
    )

    return output_path
