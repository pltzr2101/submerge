"""Tests for the config module.

Tests kept: custom parsing and observable behavior.
Tests removed: Pydantic validation (the framework does its job).
"""

from __future__ import annotations

import logging

import pytest

from submerge.config import SubtoolsSettings, get_settings_for_test


class TestSubtoolsSettingsDefaults:
    """Tests for default values."""

    def test_default_values(self, monkeypatch):
        """Verify all default values."""
        monkeypatch.delenv("SUBTOOLS_PAIRS", raising=False)
        monkeypatch.delenv("SUBTOOLS_ALLOWED_PATHS", raising=False)

        settings = get_settings_for_test()

        assert settings.pairs == []
        assert settings.required_langs == []
        assert settings.bottom_color == "#FFFFFF"
        assert settings.top_color == "#FFD700"
        assert settings.bottom_fontsize == 22
        assert settings.top_fontsize == 22
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
        assert settings.required_langs == ["fr", "pl"]

    def test_parse_multiple_pairs(self):
        """Parse multiple pairs."""
        settings = get_settings_for_test(pairs="fr-pl,en-pl")
        assert settings.pairs == [("fr", "pl"), ("en", "pl")]
        assert settings.required_langs == ["fr", "pl", "en"]

    def test_lowercase_normalization(self):
        """Codes are normalized to lowercase."""
        settings = get_settings_for_test(pairs="FR-PL")
        assert settings.pairs == [("fr", "pl")]


class TestSubtoolsSettingsComputedFields:
    """Tests for computed fields - custom logic."""

    def test_required_langs_derived_from_multiple_pairs(self):
        """Required languages are derived from all pairs."""
        settings = get_settings_for_test(pairs="fr-pl,en-pl,de-en")
        assert settings.required_langs == ["fr", "pl", "en", "de"]

    def test_required_langs_order_is_stable(self):
        """required_langs order is stable across multiple instantiations."""
        for _ in range(10):
            s = get_settings_for_test(pairs="de-ko")
            assert s.required_langs == ["de", "ko"], f"got {s.required_langs}"


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
        assert settings.bottom_fontsize == 22

        get_settings.cache_clear()


class TestWithOverrides:
    """Tests for SubtoolsSettings.with_overrides()."""

    def test_pairs_translation(self):
        """pairs key is mapped to pairs_raw automatically."""
        s = SubtoolsSettings.with_overrides(pairs="de-ko")
        assert s.pairs == [("de", "ko")]
        assert s.required_langs == ["de", "ko"]

    def test_unknown_key_is_dropped_no_error(self):
        """Unknown keys are silently dropped, no ValidationError."""
        s = SubtoolsSettings.with_overrides(nonexistent_field="x")
        assert isinstance(s, SubtoolsSettings)

    def test_unknown_key_logs_warning(self, caplog):
        """Unknown keys emit a log warning."""
        with caplog.at_level(logging.WARNING):
            SubtoolsSettings.with_overrides(zzzzz="whatever")
        assert "with_overrides: dropping unknown keys" in caplog.text
        assert "zzzzz" in caplog.text

    def test_mixed_known_and_unknown(self):
        """Known keys pass through, unknown keys are dropped."""
        s = SubtoolsSettings.with_overrides(
            pairs="fr-en",
            bottom_fontsize=24,
            bogus=999,
        )
        assert s.pairs == [("fr", "en")]
        assert s.bottom_fontsize == 24
