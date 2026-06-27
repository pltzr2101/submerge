"""Subtitle file I/O utilities shared across merge and repair modules."""

from __future__ import annotations

import logging
from pathlib import Path

import pysubs2
from pysubs2 import SSAFile

logger = logging.getLogger(__name__)


def _load_subtitle_file(path: Path) -> SSAFile:
    """Load a subtitle file with encoding handling."""
    # Lazy import to avoid circular dependency (merge.py re-exports this function)
    from .merge import InvalidSubtitleError  # noqa: PLC0415

    try:
        # pysubs2 handles encoding detection automatically
        return pysubs2.load(str(path), encoding="utf-8")
    except UnicodeDecodeError:
        # Fallback: use charset_normalizer for robust auto-detection
        logger.warning(f"UTF-8 encoding failed for {path.name}, auto-detecting...")
        try:
            from charset_normalizer import from_path as _detect

            result = _detect(path).best()
            if result is not None:
                content = str(result)
                logger.info(f"Detected encoding for {path.name}: {result.encoding}")
                return pysubs2.SSAFile.from_string(content)
            # charset_normalizer couldn't determine encoding → try EUC-KR/CP949 as
            # last resort (common for Korean subtitle files from Asian sources)
            logger.warning(f"Auto-detection failed for {path.name}, trying EUC-KR fallback...")
            for fallback_enc in ("euc-kr", "cp949", "latin-1"):
                try:
                    content = path.read_bytes().decode(fallback_enc, errors="replace")
                    subs = pysubs2.SSAFile.from_string(content)
                    logger.warning(
                        f"Loaded {path.name} with fallback encoding {fallback_enc} "
                        f"(may contain replacement chars)"
                    )
                    return subs
                except Exception:
                    continue
            raise InvalidSubtitleError(f"Could not detect encoding for {path.name}")
        except InvalidSubtitleError:
            raise
        except Exception as e:
            raise InvalidSubtitleError(f"Failed to load {path.name}: {e}") from e
    except Exception as e:
        raise InvalidSubtitleError(f"Parsing error {path.name}: {e}") from e
