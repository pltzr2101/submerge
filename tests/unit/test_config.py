"""Tests for the config module.

Tests kept: custom parsing and observable behavior.
Tests removed: Pydantic validation (the framework does its job).
"""

from __future__ import annotations

import pytest

from submerge.config import get_settings_for_test


class TestSubtoolsSettingsDefaults:
    """Tests for default values."""

    def test_default_values(self, monkeypatch):
        """Verify all default values."""
        monkeypatch.delenv("SUBTOOLS_PAIRS", raising=False)
        monkeypatch.delenv("SUBTOOLS_ALLOWED_PATHS", raising=False)

        settings = get_settings_for_test()

        assert settings.pairs == []
        assert settings.required_langs == set()
        assert settings.color_bottom == "#FFFFFF"
        assert settings.color_top == "#FFFF00"
        assert settings.fontsize == 18
        assert settings.layout == "top-bottom"


class TestSubtoolsSettingsPairs:
    """Tests for pairs parsing - custom logic."""

    def test_invalid_iso_code_raises_error(self):
        """Invalid ISO code raises error at config."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid language code"):
            get_settings_for_test(pairs="xyz-en")

    def test_parse_single_pair(self):
        """Parse a single pair."""
        settings = get_settings_for_test(pairs="fr-pl")
        assert settings.pairs == [("fr", "pl")]
        assert settings.required_langs == {"fr", "pl"}

    def test_parse_multiple_pairs(self):
        """Parse multiple pairs."""
        settings = get_settings_for_test(pairs="fr-pl,en-pl")
        assert settings.pairs == [("fr", "pl"), ("en", "pl")]
        assert settings.required_langs == {"fr", "pl", "en"}

    def test_lowercase_normalization(self):
        """Codes are normalized to lowercase."""
        settings = get_settings_for_test(pairs="FR-PL")
        assert settings.pairs == [("fr", "pl")]


class TestSubtoolsSettingsComputedFields:
    """Tests for computed fields - custom logic."""

    def test_required_langs_derived_from_multiple_pairs(self):
        """Required languages are derived from all pairs."""
        settings = get_settings_for_test(pairs="fr-pl,en-pl,de-en")
        assert settings.required_langs == {"fr", "pl", "en", "de"}

    def test_margin_top_stacked_calculation(self):
        """Margin top is calculated from fontsize."""
        settings = get_settings_for_test(fontsize=18)
        assert settings.margin_top_stacked == 10 + int(18 * 2.5)  # 55

        settings = get_settings_for_test(fontsize=24)
        assert settings.margin_top_stacked == 10 + int(24 * 2.5)  # 70


class TestSubtoolsSettingsFromEnv:
    """Tests with real environment variables."""

    def test_loads_from_environment(self, monkeypatch):
        """Settings are loaded from env vars."""
        monkeypatch.setenv("SUBTOOLS_PAIRS", "fr-en")
        monkeypatch.setenv("SUBTOOLS_FONTSIZE", "20")

        from submerge.config import get_settings
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.pairs == [("fr", "en")]
        assert settings.fontsize == 20

        get_settings.cache_clear()
