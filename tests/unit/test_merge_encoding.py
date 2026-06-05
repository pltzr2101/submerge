"""Tests for encoding fallback in _load_subtitle_file."""

from __future__ import annotations

from pathlib import Path

import pytest

from submerge.merge import InvalidSubtitleError, MergeConfig, merge_bilingual
from tests.conftest import FIXTURES_DIR

_ENCODING_CONFIG = MergeConfig(
    fontsize_bottom=20,
    fontsize_top=20,
    outline_bottom=2.0,
    outline_top=2.0,
)


class TestEncodingFallback:
    """Verify EUC-KR / CP949 encoded SRT files can be loaded."""

    def test_euc_kr_subtitle_loaded(self, tmp_path: Path, sample_srt_pl: Path):
        """EUC-KR encoded Korean SRT is loaded without InvalidSubtitleError."""
        euc_kr_file = FIXTURES_DIR / "sample_euc_kr.srt"
        output = tmp_path / "output.ass"
        try:
            merge_bilingual(euc_kr_file, sample_srt_pl, output, _ENCODING_CONFIG)
        except InvalidSubtitleError as exc:
            pytest.fail(f"EUC-KR file should be loadable, got {exc}")

        assert output.exists()

    def test_cp949_subtitle_loaded(self, tmp_path: Path, sample_srt_pl: Path):
        """CP949 encoded Korean SRT is loaded without InvalidSubtitleError."""
        cp949_file = FIXTURES_DIR / "sample_cp949.srt"
        output = tmp_path / "output.ass"
        try:
            merge_bilingual(cp949_file, sample_srt_pl, output, _ENCODING_CONFIG)
        except InvalidSubtitleError as exc:
            pytest.fail(f"CP949 file should be loadable, got {exc}")

        assert output.exists()

    def test_garbage_file_raises_error(self, tmp_path: Path, sample_srt_pl: Path):
        """Random bytes still raise InvalidSubtitleError."""
        garbage_file = FIXTURES_DIR / "sample_garbage.bin"
        with pytest.raises(InvalidSubtitleError):
            merge_bilingual(garbage_file, sample_srt_pl, tmp_path / "output.ass", _ENCODING_CONFIG)
