"""Shared fixtures for all tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from submerge.config import get_settings_for_test

# Path to fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# =============================================================================
# Configuration fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Give each test an isolated SUBTOOLS_CONFIG_DIR and initialized DB."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("SUBTOOLS_CONFIG_DIR", str(config_dir))
    from submerge.config import get_settings

    get_settings.cache_clear()
    # init_db must be called explicitly since DDL is only in init_db
    from submerge.queue import init_db

    init_db()


# =============================================================================
# File fixtures
# =============================================================================


@pytest.fixture
def sample_srt_fr() -> Path:
    """French test SRT file."""
    return FIXTURES_DIR / "sample_fr.srt"


@pytest.fixture
def sample_srt_pl() -> Path:
    """Polish test SRT file."""
    return FIXTURES_DIR / "sample_pl.srt"


# =============================================================================
# Configuration fixtures
# =============================================================================


@pytest.fixture
def settings_fr_pl_en():
    """Settings with fr-pl and en-pl pairs."""
    return get_settings_for_test(
        pairs="fr-pl,en-pl",
        bottom_color="#FFFFFF",
        top_color="#FFFF00",
        fontsize=18,
        layout="top-bottom",
    )


@pytest.fixture
def settings_fr_pl():
    """Settings with single fr-pl pair."""
    return get_settings_for_test(pairs="fr-pl")


# =============================================================================
# Inline subtitle fixtures (for integration tests)
# =============================================================================

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


@pytest.fixture
def srt_content_fr() -> str:
    """French SRT content."""
    return SAMPLE_SRT_FR


@pytest.fixture
def srt_content_pl() -> str:
    """Polish SRT content."""
    return SAMPLE_SRT_PL


@pytest.fixture
def srt_content_en() -> str:
    """English SRT content."""
    return SAMPLE_SRT_EN
