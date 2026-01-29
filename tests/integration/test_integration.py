"""Integration tests - verify real system behavior.

These tests use real files and no mocks.
They verify that the system works end-to-end.
"""

from __future__ import annotations

import time
from pathlib import Path


from submerge.config import get_settings_for_test
from submerge.hook import process_hook
from submerge.merge import MergeConfig, merge_bilingual


# Realistic subtitle fixtures
SAMPLE_SRT_FR = """1
00:00:01,000 --> 00:00:03,000
Bonjour, comment ça va ?

2
00:00:05,000 --> 00:00:07,000
Très bien, merci !

3
00:00:10,000 --> 00:00:12,000
À bientôt !
"""

SAMPLE_SRT_PL = """1
00:00:01,000 --> 00:00:03,000
Cześć, jak się masz?

2
00:00:05,000 --> 00:00:07,000
Bardzo dobrze, dziękuję!

3
00:00:10,000 --> 00:00:12,000
Do zobaczenia!
"""

SAMPLE_SRT_EN = """1
00:00:01,000 --> 00:00:03,000
Hello, how are you?

2
00:00:05,000 --> 00:00:07,000
Very well, thank you!

3
00:00:10,000 --> 00:00:12,000
See you soon!
"""


class TestMergeIntegration:
    """Integration tests for bilingual merge."""

    def test_merge_produces_valid_ass_with_both_languages(self, tmp_path: Path):
        """ASS file contains both languages."""
        fr_srt = tmp_path / "movie.fr.srt"
        pl_srt = tmp_path / "movie.pl.srt"
        output = tmp_path / "movie.fr-pl.ass"

        fr_srt.write_text(SAMPLE_SRT_FR)
        pl_srt.write_text(SAMPLE_SRT_PL)

        merge_bilingual(fr_srt, pl_srt, output)

        assert output.exists()
        content = output.read_text()

        # Verify both languages are present
        assert "Bonjour" in content
        assert "Cześć" in content
        assert "Très bien" in content
        assert "Bardzo dobrze" in content
        assert "À bientôt" in content
        assert "Do zobaczenia" in content

    def test_merge_stacked_layout_produces_valid_ass(self, tmp_path: Path):
        """Stacked layout produces valid ASS file."""
        fr_srt = tmp_path / "movie.fr.srt"
        pl_srt = tmp_path / "movie.pl.srt"
        output = tmp_path / "movie.fr-pl.ass"

        fr_srt.write_text(SAMPLE_SRT_FR)
        pl_srt.write_text(SAMPLE_SRT_PL)

        config = MergeConfig(layout="stacked", fontsize=20)
        merge_bilingual(fr_srt, pl_srt, output, config)

        assert output.exists()
        content = output.read_text()

        # Verify ASS structure
        assert "[Script Info]" in content
        assert "[V4+ Styles]" in content
        assert "[Events]" in content
        assert "Bonjour" in content
        assert "Cześć" in content

    def test_merge_preserves_timing(self, tmp_path: Path):
        """Merge preserves subtitle timings."""
        fr_srt = tmp_path / "movie.fr.srt"
        pl_srt = tmp_path / "movie.pl.srt"
        output = tmp_path / "movie.fr-pl.ass"

        fr_srt.write_text(SAMPLE_SRT_FR)
        pl_srt.write_text(SAMPLE_SRT_PL)

        merge_bilingual(fr_srt, pl_srt, output)

        content = output.read_text()

        # Timings must be present (ASS format: 0:00:01.00)
        assert "0:00:01" in content
        assert "0:00:05" in content
        assert "0:00:10" in content


class TestHookIntegration:
    """Integration tests for complete hook."""

    def test_hook_creates_bilingual_files_when_all_langs_present(self, tmp_path: Path):
        """Hook creates .ass files when all languages are present."""
        video = tmp_path / "Movie.mkv"
        video.touch()

        (tmp_path / "Movie.fr.srt").write_text(SAMPLE_SRT_FR)
        (tmp_path / "Movie.pl.srt").write_text(SAMPLE_SRT_PL)
        (tmp_path / "Movie.en.srt").write_text(SAMPLE_SRT_EN)

        settings = get_settings_for_test(pairs="fr-pl,en-pl")

        result = process_hook(video, tmp_path / "Movie.fr.srt", "fr", settings)

        assert result.status == "merged"
        assert (tmp_path / "Movie.fr-pl.ass").exists()
        assert (tmp_path / "Movie.en-pl.ass").exists()

        # Verify fr-pl content
        fr_pl_content = (tmp_path / "Movie.fr-pl.ass").read_text()
        assert "Bonjour" in fr_pl_content
        assert "Cześć" in fr_pl_content

        # Verify en-pl content
        en_pl_content = (tmp_path / "Movie.en-pl.ass").read_text()
        assert "Hello" in en_pl_content
        assert "Cześć" in en_pl_content

    def test_hook_returns_waiting_when_lang_missing(self, tmp_path: Path):
        """Hook returns waiting if a language is missing."""
        video = tmp_path / "Movie.mkv"
        video.touch()

        (tmp_path / "Movie.fr.srt").write_text(SAMPLE_SRT_FR)
        (tmp_path / "Movie.pl.srt").write_text(SAMPLE_SRT_PL)
        # No Movie.en.srt

        settings = get_settings_for_test(pairs="fr-pl,en-pl")

        result = process_hook(video, tmp_path / "Movie.fr.srt", "fr", settings)

        assert result.status == "waiting"
        assert "en" in result.missing
        assert set(result.present) == {"fr", "pl"}

    def test_hook_skips_when_ass_already_exists_and_newer(self, tmp_path: Path):
        """Hook skips if .ass exist and are newer."""
        video = tmp_path / "Movie.mkv"
        video.touch()

        (tmp_path / "Movie.fr.srt").write_text(SAMPLE_SRT_FR)
        (tmp_path / "Movie.pl.srt").write_text(SAMPLE_SRT_PL)
        (tmp_path / "Movie.en.srt").write_text(SAMPLE_SRT_EN)

        time.sleep(0.05)

        # Create .ass files after .srt files
        (tmp_path / "Movie.fr-pl.ass").write_text("existing")
        (tmp_path / "Movie.en-pl.ass").write_text("existing")

        settings = get_settings_for_test(pairs="fr-pl,en-pl")

        result = process_hook(video, tmp_path / "Movie.fr.srt", "fr", settings)

        assert result.status == "skipped"
        assert result.reason == "already_exists"

    def test_hook_regenerates_when_srt_newer_than_ass(self, tmp_path: Path):
        """Hook regenerates if an .srt is newer than .ass."""
        video = tmp_path / "Movie.mkv"
        video.touch()

        # Create .ass files first
        (tmp_path / "Movie.fr-pl.ass").write_text("old")
        (tmp_path / "Movie.en-pl.ass").write_text("old")

        time.sleep(0.05)

        # Create .srt files after
        (tmp_path / "Movie.fr.srt").write_text(SAMPLE_SRT_FR)
        (tmp_path / "Movie.pl.srt").write_text(SAMPLE_SRT_PL)
        (tmp_path / "Movie.en.srt").write_text(SAMPLE_SRT_EN)

        settings = get_settings_for_test(pairs="fr-pl,en-pl")

        result = process_hook(video, tmp_path / "Movie.fr.srt", "fr", settings)

        assert result.status == "merged"

        # Verify content was updated
        content = (tmp_path / "Movie.fr-pl.ass").read_text()
        assert "Bonjour" in content  # New content, not "old"


class TestBazarrWorkflow:
    """Simulate complete Bazarr workflow."""

    def test_bazarr_sequential_downloads_two_langs(self, tmp_path: Path):
        """Simulate Bazarr downloading 2 subtitles one by one."""
        video = tmp_path / "Show.S01E01.mkv"
        video.touch()

        settings = get_settings_for_test(pairs="fr-pl")

        # Bazarr downloads FR
        (tmp_path / "Show.S01E01.fr.srt").write_text(SAMPLE_SRT_FR)
        result1 = process_hook(video, tmp_path / "Show.S01E01.fr.srt", "fr", settings)
        assert result1.status == "waiting"
        assert result1.missing == ["pl"]

        # Bazarr downloads PL
        (tmp_path / "Show.S01E01.pl.srt").write_text(SAMPLE_SRT_PL)
        result2 = process_hook(video, tmp_path / "Show.S01E01.pl.srt", "pl", settings)
        assert result2.status == "merged"

        # Verify final result
        assert (tmp_path / "Show.S01E01.fr-pl.ass").exists()
        content = (tmp_path / "Show.S01E01.fr-pl.ass").read_text()
        assert "Bonjour" in content
        assert "Cześć" in content

    def test_bazarr_sequential_downloads_three_langs(self, tmp_path: Path):
        """Simulate Bazarr downloading 3 subtitles for 2 pairs."""
        video = tmp_path / "Show.S01E01.mkv"
        video.touch()

        settings = get_settings_for_test(pairs="fr-pl,en-pl")

        # Bazarr downloads FR - waiting (missing pl, en)
        (tmp_path / "Show.S01E01.fr.srt").write_text(SAMPLE_SRT_FR)
        result1 = process_hook(video, tmp_path / "Show.S01E01.fr.srt", "fr", settings)
        assert result1.status == "waiting"
        assert set(result1.missing) == {"pl", "en"}

        # Bazarr downloads EN - waiting (missing pl)
        (tmp_path / "Show.S01E01.en.srt").write_text(SAMPLE_SRT_EN)
        result2 = process_hook(video, tmp_path / "Show.S01E01.en.srt", "en", settings)
        assert result2.status == "waiting"
        assert result2.missing == ["pl"]

        # Bazarr downloads PL - merged (all languages)
        (tmp_path / "Show.S01E01.pl.srt").write_text(SAMPLE_SRT_PL)
        result3 = process_hook(video, tmp_path / "Show.S01E01.pl.srt", "pl", settings)
        assert result3.status == "merged"

        # Verify both files
        assert (tmp_path / "Show.S01E01.fr-pl.ass").exists()
        assert (tmp_path / "Show.S01E01.en-pl.ass").exists()

    def test_hook_with_single_pair_config(self, tmp_path: Path):
        """Test with single pair configured."""
        video = tmp_path / "Movie.mkv"
        video.touch()

        (tmp_path / "Movie.de.srt").write_text(SAMPLE_SRT_FR)  # FR content but DE file
        (tmp_path / "Movie.en.srt").write_text(SAMPLE_SRT_EN)

        settings = get_settings_for_test(pairs="de-en")

        result = process_hook(video, tmp_path / "Movie.de.srt", "de", settings)

        assert result.status == "merged"
        assert (tmp_path / "Movie.de-en.ass").exists()
