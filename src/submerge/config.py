"""Centralized sub-tools configuration via environment variables."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ISO 639-1 codes (subset des plus courants, extensible)
ISO_639_1_CODES = {
    "aa",
    "ab",
    "af",
    "ak",
    "am",
    "ar",
    "as",
    "ay",
    "az",
    "ba",
    "be",
    "bg",
    "bh",
    "bi",
    "bn",
    "bo",
    "br",
    "bs",
    "ca",
    "ce",
    "ch",
    "co",
    "cs",
    "cy",
    "da",
    "de",
    "dv",
    "dz",
    "el",
    "en",
    "eo",
    "es",
    "et",
    "eu",
    "fa",
    "fi",
    "fj",
    "fo",
    "fr",
    "fy",
    "ga",
    "gd",
    "gl",
    "gn",
    "gu",
    "ha",
    "he",
    "hi",
    "hr",
    "ht",
    "hu",
    "hy",
    "ia",
    "id",
    "ie",
    "ig",
    "ik",
    "is",
    "it",
    "iu",
    "ja",
    "jv",
    "ka",
    "kg",
    "ki",
    "kk",
    "kl",
    "km",
    "kn",
    "ko",
    "kr",
    "ks",
    "ku",
    "ky",
    "la",
    "lb",
    "lg",
    "li",
    "ln",
    "lo",
    "lt",
    "lv",
    "mg",
    "mh",
    "mi",
    "mk",
    "ml",
    "mn",
    "mr",
    "ms",
    "mt",
    "my",
    "na",
    "nb",
    "nd",
    "ne",
    "nl",
    "nn",
    "no",
    "nv",
    "ny",
    "oc",
    "om",
    "or",
    "os",
    "pa",
    "pi",
    "pl",
    "ps",
    "pt",
    "qu",
    "rm",
    "rn",
    "ro",
    "ru",
    "rw",
    "sa",
    "sc",
    "sd",
    "se",
    "sg",
    "si",
    "sk",
    "sl",
    "sm",
    "sn",
    "so",
    "sq",
    "sr",
    "ss",
    "st",
    "su",
    "sv",
    "sw",
    "ta",
    "te",
    "tg",
    "th",
    "ti",
    "tk",
    "tl",
    "tn",
    "to",
    "tr",
    "ts",
    "tt",
    "tw",
    "ty",
    "ug",
    "uk",
    "ur",
    "uz",
    "ve",
    "vi",
    "vo",
    "wa",
    "wo",
    "xh",
    "yi",
    "yo",
    "za",
    "zh",
    "zu",
}

HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _parse_pairs_string(pairs_str: str) -> list[tuple[str, str]]:
    """Parse 'fr-pl,en-pl' -> [('fr', 'pl'), ('en', 'pl')]"""
    if not pairs_str:
        return []

    pairs = []
    for pair_str in pairs_str.split(","):
        pair_str = pair_str.strip()
        if not pair_str:
            continue
        if "-" not in pair_str:
            raise ValueError(f"Invalid pair format: '{pair_str}'. Expected 'lang1-lang2'")
        parts = pair_str.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid pair format: '{pair_str}'. Expected exactly 2 languages")
        lang1, lang2 = parts[0].strip().lower(), parts[1].strip().lower()

        # Validate ISO 639-1
        if lang1 not in ISO_639_1_CODES:
            raise ValueError(f"Invalid language code: '{lang1}'. Must be ISO 639-1 (2 letters)")
        if lang2 not in ISO_639_1_CODES:
            raise ValueError(f"Invalid language code: '{lang2}'. Must be ISO 639-1 (2 letters)")

        pairs.append((lang1, lang2))
    return pairs


logger = logging.getLogger(__name__)


class SubtoolsSettings(BaseSettings):
    """Configuration sub-tools depuis variables d'environnement.

    Environment variables (all prefixed with SUBTOOLS_):
    - SUBTOOLS_PAIRS: Language pairs (required for API), e.g., "fr-pl,en-pl"
    - SUBTOOLS_MEDIA_ROOT: Root directory for media files, default "/data"
    - SUBTOOLS_POLL_INTERVAL: Polling interval in seconds for retry, default 60
    - SUBTOOLS_COLOR_BOTTOM: Hex color for bottom subtitle, default "#FFFFFF"
    - SUBTOOLS_COLOR_TOP: Hex color for top subtitle, default "#FFD700"
    - SUBTOOLS_FONTSIZE: Font size (8-72), default 18
    - SUBTOOLS_LAYOUT: "top-bottom" or "stacked", default "top-bottom"
    """

    model_config = SettingsConfigDict(
        env_prefix="SUBTOOLS_",
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,  # Allow both field name and alias
    )

    # Language pairs - env var: SUBTOOLS_PAIRS
    # Using validation_alias to map the env var (prefix is ignored with alias)
    pairs_raw: str = Field(default="", validation_alias="SUBTOOLS_PAIRS")

    # Media scanning
    media_root: str = "/data"
    poll_interval: int = 60
    retry_timeout_h: int = 48

    # Config persistence (presets, settings.json)
    config_dir: str = "/config"

    # UI auth
    ui_password: str = ""
    ui_user: str = "admin"
    rate_limit_rpm: int = 30  # 0 = disabled

    # Appearance
    fontsize: int = 18
    layout: Literal["top-bottom", "stacked"] = "top-bottom"

    # Bottom subtitle style (e.g., DE)
    bottom_fontsize: int = 20
    bottom_color: str = Field(
        default="#FFFFFF",
        validation_alias="SUBTOOLS_COLOR_BOTTOM",
    )
    bottom_outline_color: str = "#000000"
    bottom_outline: float = 2.0
    bottom_shadow: float = 1.0
    bottom_bold: bool = False
    bottom_margin_v: int = 30
    bottom_margin_h: int = 20
    bottom_spacing: float = 0.0
    font_bottom: str = ""  # Empty = OS system font fallback

    # Top subtitle style (e.g., KO)
    top_fontsize: int = 18
    top_color: str = Field(
        default="#FFD700",
        validation_alias="SUBTOOLS_COLOR_TOP",
    )
    top_outline_color: str = "#000000"
    top_outline: float = 2.0
    top_shadow: float = 1.0
    top_bold: bool = False
    top_margin_v: int = 15
    top_margin_h: int = 20
    top_spacing: float = 0.0
    font_top: str = ""  # Empty = OS system font fallback (CJK-safe)

    # Stacked layout
    stacked_gap: int = 8

    @classmethod
    def with_overrides(cls, **overrides: Any) -> SubtoolsSettings:
        """Create a new instance with the given field overrides applied.

        Maps the user-friendly ``pairs`` key to ``pairs_raw`` automatically.
        Unknown keys are dropped with a warning.
        """
        if "pairs" in overrides:
            overrides["pairs_raw"] = overrides.pop("pairs")
        known = set(cls.model_fields.keys())
        dropped = [k for k in overrides if k not in known]
        if dropped:
            logger.warning(f"with_overrides: dropping unknown keys {dropped}")
        filtered = {k: v for k, v in overrides.items() if k in known}
        return cls(**filtered)

    @field_validator("pairs_raw")
    @classmethod
    def validate_pairs_raw(cls, v: str) -> str:
        """Validate pairs string format."""
        _parse_pairs_string(v)  # Raises ValueError if invalid
        return v

    @field_validator("bottom_color", "top_color", "bottom_outline_color", "top_outline_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        """Validate hex color format #RRGGBB"""
        if not HEX_COLOR_PATTERN.match(v):
            raise ValueError(f"Invalid color: '{v}'. Expected format: #RRGGBB")
        return v.upper()

    @field_validator("fontsize", "bottom_fontsize", "top_fontsize")
    @classmethod
    def validate_fontsize(cls, v: int) -> int:
        """Validate fontsize range"""
        if not 8 <= v <= 72:
            raise ValueError(f"Invalid fontsize: {v}. Must be between 8 and 72")
        return v

    @computed_field
    @property
    def pairs(self) -> list[tuple[str, str]]:
        """Parsed language pairs."""
        return _parse_pairs_string(self.pairs_raw)

    @computed_field
    @property
    def required_langs(self) -> list[str]:
        """Required languages in stable order derived from pairs."""
        pairs = self.pairs
        seen: dict[str, None] = {}
        for bottom, top in pairs:
            seen.setdefault(bottom, None)
            seen.setdefault(top, None)
        return list(seen.keys())


@lru_cache
def get_settings() -> SubtoolsSettings:
    """Return settings (cached). Crashes if config is invalid."""
    return SubtoolsSettings()


def get_settings_for_test(**overrides) -> SubtoolsSettings:
    """Factory for tests - allows overriding values.

    Supports user-friendly parameter names that map to internal fields:
    - pairs -> pairs_raw
    """
    # Map user-friendly names to internal field names
    if "pairs" in overrides:
        overrides["pairs_raw"] = overrides.pop("pairs")
    return SubtoolsSettings(**overrides)
