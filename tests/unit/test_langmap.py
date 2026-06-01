"""Tests for the langmap module."""

from __future__ import annotations

import pytest

from submerge.langmap import get_all_aliases, normalize_lang


class TestNormalizeLang:
    """Tests for language code normalization."""

    def test_iso_639_1_passthrough(self):
        assert normalize_lang("de") == "de"
        assert normalize_lang("en") == "en"
        assert normalize_lang("ko") == "ko"
        assert normalize_lang("fr") == "fr"

    def test_iso_639_2_t(self):
        """3-letter ISO 639-2/T codes normalize to 2-letter."""
        assert normalize_lang("deu") == "de"
        assert normalize_lang("eng") == "en"
        assert normalize_lang("kor") == "ko"
        assert normalize_lang("fra") == "fr"

    def test_iso_639_2_b(self):
        """3-letter bibliographic codes normalize."""
        assert normalize_lang("ger") == "de"
        assert normalize_lang("fre") == "fr"
        assert normalize_lang("chi") == "zh"

    def test_locale_style(self):
        """Locale-style codes (de-DE, ko-KR) normalize."""
        assert normalize_lang("de-DE") == "de"
        assert normalize_lang("de_DE") == "de"
        assert normalize_lang("ko-KR") == "ko"
        assert normalize_lang("ko_KR") == "ko"
        assert normalize_lang("en-US") == "en"
        assert normalize_lang("en_GB") == "en"

    def test_case_insensitive(self):
        assert normalize_lang("DE") == "de"
        assert normalize_lang("Deu") == "de"
        assert normalize_lang("GER") == "de"
        assert normalize_lang("En-US") == "en"
        assert normalize_lang("KO") == "ko"

    def test_unknown_code(self):
        assert normalize_lang("xyz") is None
        assert normalize_lang("") is None
        assert normalize_lang(None) is None  # type: ignore[arg-type]

    def test_norwegian_variants(self):
        assert normalize_lang("nb") == "no"
        assert normalize_lang("nob") == "no"
        assert normalize_lang("nn") == "no"
        assert normalize_lang("nno") == "no"


class TestGetAllAliases:
    """Tests for alias list generation."""

    def test_returns_list_with_self(self):
        aliases = get_all_aliases("de")
        assert "de" in aliases
        assert len(aliases) > 1

    def test_includes_3_letter_codes(self):
        aliases = get_all_aliases("de")
        assert "deu" in aliases
        assert "ger" in aliases

    def test_includes_locale_codes(self):
        aliases = get_all_aliases("de")
        assert "de-DE" in aliases
        assert "de_DE" in aliases

    def test_unknown_lang_returns_self(self):
        aliases = get_all_aliases("xyz")
        assert aliases == ["xyz"]


class TestFindSubtitlePathWithAliases:
    """Integration test: hook uses langmap aliases for file discovery."""

    def test_finds_3_letter_code_files(self, tmp_path):
        from submerge.hook import find_subtitle_path

        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.deu.srt").touch()

        result = find_subtitle_path(video, "de")
        assert result is not None
        assert result.name == "Show.deu.srt"

    def test_finds_locale_style_files(self, tmp_path):
        from submerge.hook import find_subtitle_path

        video = tmp_path / "Show.mkv"
        video.touch()
        (tmp_path / "Show.ko-KR.srt").touch()

        result = find_subtitle_path(video, "ko")
        assert result is not None
        assert result.name == "Show.ko-KR.srt"

    def test_normalizes_incoming_lang(self, tmp_path):
        """validate_lang should normalize 'deu' -> 'de' for pairs."""
        from submerge.config import get_settings_for_test
        from submerge.hook import validate_lang

        settings = get_settings_for_test(pairs="de-ko")
        result = validate_lang("deu", settings)
        assert result == "de"

    def test_normalizes_locale_lang(self, tmp_path):
        from submerge.config import get_settings_for_test
        from submerge.hook import validate_lang

        settings = get_settings_for_test(pairs="de-ko")
        result = validate_lang("ko-KR", settings)
        assert result == "ko"

    def test_rejects_unknown_after_normalization(self):
        from submerge.config import get_settings_for_test
        from submerge.hook import InvalidLanguageError, validate_lang

        settings = get_settings_for_test(pairs="de-ko")
        with pytest.raises(InvalidLanguageError):
            validate_lang("fra", settings)  # French -> 'fr', not in pairs
