"""Business logic for Bazarr hook for automatic bilingual subtitle generation."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock, Timeout

from .config import SubtoolsSettings, get_settings
from .merge import MergeConfig, merge_bilingual

logger = logging.getLogger(__name__)

# Constantes
LOCK_TIMEOUT = 5  # Secondes

# ISO 639-1 -> ISO 639-2/T mapping for 3-letter code fallback lookups
ISO_639_1_TO_2 = {
    "de": "deu", "en": "eng", "ko": "kor", "fr": "fra", "pl": "pol",
    "es": "spa", "it": "ita", "ja": "jpn", "zh": "zho", "ru": "rus",
    "pt": "por", "ar": "ara", "nl": "nld", "sv": "swe", "no": "nor",
    "da": "dan", "fi": "fin", "tr": "tur", "el": "ell", "cs": "ces",
    "hu": "hun", "ro": "ron", "uk": "ukr", "th": "tha", "vi": "vie",
    "hi": "hin", "bn": "ben", "id": "ind", "ms": "msa", "tl": "tgl",
}

# Track active polling jobs: video_path -> threading.Event (set to cancel)
_polling_jobs: dict[str, threading.Event] = {}


class HookError(Exception):
    """Base error for hook."""


class InvalidLanguageError(HookError):
    """Unsupported language."""


class ProcessingError(HookError):
    """Error during processing."""


@dataclass
class HookResult:
    """Hook result."""

    status: str  # "merged", "waiting", "skipped", "already_processing", "polling"
    files: list[str] | None = None
    present: list[str] | None = None
    missing: list[str] | None = None
    reason: str | None = None


def validate_lang(lang: str, settings: SubtoolsSettings | None = None) -> str:
    """Validate and normalize a language.

    Args:
        lang: Language code (e.g., fr, pl, en)
        settings: Settings to get required languages

    Returns:
        Normalized lowercase language

    Raises:
        InvalidLanguageError: If language is not in configured pairs
    """
    settings = settings or get_settings()
    lang_lower = lang.lower().strip()
    if lang_lower not in settings.required_langs:
        raise InvalidLanguageError(
            f"Invalid language: {lang}. Must be one of: {', '.join(sorted(settings.required_langs))}"
        )
    return lang_lower


def _get_lang_patterns(video_stem: str, lang: str) -> list[str]:
    """Build all possible filename patterns for a language code.

    Handles both 2-letter (ISO 639-1) and 3-letter (ISO 639-2) codes
    that Bazarr/Lingarr might use.

    Args:
        video_stem: Video filename stem (without extension)
        lang: 2-letter language code

    Returns:
        List of filename patterns to try, in priority order
    """
    lang3 = ISO_639_1_TO_2.get(lang, lang)
    extensions = [".srt", ".ass"]
    hi_extensions = [".hi.srt", ".hi.ass"]
    forced = [".forced.srt", ".forced.ass"]

    patterns = []
    # Priority: 2-letter regular > 2-letter HI > 3-letter regular > 3-letter HI > forced variants
    for ext in extensions:
        patterns.append(f"{video_stem}.{lang}{ext}")
    for ext in hi_extensions:
        patterns.append(f"{video_stem}.{lang}{ext}")
    if lang3 != lang:
        for ext in extensions:
            patterns.append(f"{video_stem}.{lang3}{ext}")
        for ext in hi_extensions:
            patterns.append(f"{video_stem}.{lang3}{ext}")
    for ext in forced:
        patterns.append(f"{video_stem}.{lang}{ext}")
        if lang3 != lang:
            patterns.append(f"{video_stem}.{lang3}{ext}")

    return patterns


def find_subtitle_path(video_path: Path, lang: str) -> Path | None:
    """Find subtitle file for a language.

    Searches in order: .srt, .ass, .hi.srt, .hi.ass
    Also tries 3-letter ISO 639-2 codes (e.g., 'deu' for 'de').

    Args:
        video_path: Path to video file
        lang: Language code (fr, pl, en)

    Returns:
        Path to subtitle file or None if not found
    """
    video_dir = video_path.parent
    video_stem = video_path.stem

    patterns = _get_lang_patterns(video_stem, lang)

    for pattern in patterns:
        path = video_dir / pattern
        if path.exists():
            logger.debug(f"Found: {path}")
            return path

    logger.debug(f"Not found: {lang} for {video_stem}")
    return None


def check_all_languages_present(
    video_path: Path,
    settings: SubtoolsSettings | None = None,
) -> dict[str, Path] | None:
    """Check if all required languages are present.

    Args:
        video_path: Path to video file
        settings: Settings to get required languages

    Returns:
        Dict {lang: path} if all languages present, None otherwise
    """
    settings = settings or get_settings()
    result = {}
    for lang in settings.required_langs:
        path = find_subtitle_path(video_path, lang)
        if path is None:
            return None
        result[lang] = path
    return result


def get_present_and_missing(
    video_path: Path,
    settings: SubtoolsSettings | None = None,
) -> tuple[list[str], list[str]]:
    """Return present and missing languages.

    Args:
        video_path: Path to video file
        settings: Settings to get required languages

    Returns:
        Tuple (present_languages, missing_languages)
    """
    settings = settings or get_settings()
    present = []
    missing = []
    for lang in sorted(settings.required_langs):
        if find_subtitle_path(video_path, lang):
            present.append(lang)
        else:
            missing.append(lang)
    return present, missing


def _cancel_polling(video_path: Path) -> None:
    """Cancel any active polling for a video."""
    key = str(video_path.resolve())
    event = _polling_jobs.pop(key, None)
    if event is not None:
        event.set()
        logger.info(f"Polling cancelled for {video_path.name}")


def _polling_worker(
    video_path: Path,
    settings: SubtoolsSettings,
    cancel_event: threading.Event,
) -> None:
    """Background worker that polls for missing languages and triggers merge.

    Args:
        video_path: Path to video file
        settings: Configuration
        cancel_event: Set to stop polling
    """
    poll_interval = settings.poll_interval
    max_attempts = 30  # Max 30 retries (~30 min with 60s interval)

    logger.info(
        f"Polling started for {video_path.name} "
        f"(interval={poll_interval}s, max={max_attempts} attempts)"
    )

    for attempt in range(1, max_attempts + 1):
        if cancel_event.wait(timeout=poll_interval):
            logger.info(f"Polling cancelled for {video_path.name} after {attempt} attempts")
            return

        try:
            sub_paths = check_all_languages_present(video_path, settings)
            if sub_paths is not None:
                logger.info(
                    f"All languages present for {video_path.name} (attempt {attempt})"
                )

                if should_skip_existing(video_path, sub_paths, settings):
                    logger.info(f"Skipping {video_path.name}: outputs already up-to-date")
                    return

                created_files = process_bilingual_merge(video_path, sub_paths, settings)
                logger.info(
                    f"Polling merge complete for {video_path.name}: "
                    f"{[f.name for f in created_files]}"
                )
                return

            present, missing = get_present_and_missing(video_path, settings)
            logger.debug(
                f"Polling {video_path.name} (attempt {attempt}/{max_attempts}): "
                f"present={present}, missing={missing}"
            )
        except Exception as e:
            logger.error(f"Polling error for {video_path.name} (attempt {attempt}): {e}")

    logger.warning(f"Polling exhausted for {video_path.name} after {max_attempts} attempts")

    # Cleanup
    key = str(video_path.resolve())
    _polling_jobs.pop(key, None)


def start_polling(
    video_path: Path,
    settings: SubtoolsSettings | None = None,
) -> bool:
    """Start a background polling job for a video.

    If a polling job already exists for this video, it's cancelled first.

    Args:
        video_path: Path to video file
        settings: Configuration

    Returns:
        True if polling was started
    """
    settings = settings or get_settings()

    # Cancel any existing polling for this file
    _cancel_polling(video_path)

    key = str(video_path.resolve())
    cancel_event = threading.Event()
    _polling_jobs[key] = cancel_event

    thread = threading.Thread(
        target=_polling_worker,
        args=(video_path, settings, cancel_event),
        daemon=True,
        name=f"submerge-poll-{video_path.name}",
    )
    thread.start()
    return True


def get_output_path(video_path: Path, lang_bottom: str, lang_top: str) -> Path:
    """Generate output path for a language pair.

    Args:
        video_path: Path to video file
        lang_bottom: Language displayed at bottom
        lang_top: Language displayed at top

    Returns:
        Path to output .ass file
    """
    return video_path.parent / f"{video_path.stem}.{lang_bottom}-{lang_top}.ass"


def should_skip_existing(
    video_path: Path,
    sub_paths: dict[str, Path],
    settings: SubtoolsSettings | None = None,
) -> bool:
    """Check if .ass files exist and are newer than sources.

    Args:
        video_path: Path to video file
        sub_paths: Dict {lang: path} of source subtitles
        settings: Settings to get configured pairs

    Returns:
        True if all .ass exist and are newer than their sources
    """
    settings = settings or get_settings()

    for lang_bottom, lang_top in settings.pairs:
        output_path = get_output_path(video_path, lang_bottom, lang_top)

        if not output_path.exists():
            logger.debug(f"Skip check: {output_path} does not exist")
            return False

        output_mtime = output_path.stat().st_mtime

        # Check that .ass is newer than both sources
        for lang in (lang_bottom, lang_top):
            if lang not in sub_paths:
                return False
            source_path = sub_paths[lang]
            source_mtime = source_path.stat().st_mtime
            if source_mtime > output_mtime:
                logger.debug(f"Skip check: {source_path} newer than {output_path}")
                return False

    logger.info("Existing .ass files are up to date, skipping")
    return True


def get_lock_path(video_path: Path) -> Path:
    """Return the lock file path for a video."""
    return video_path.parent / f".{video_path.stem}.sub-tools.lock"


def process_bilingual_merge(
    video_path: Path,
    sub_paths: dict[str, Path],
    settings: SubtoolsSettings | None = None,
) -> list[Path]:
    """Generate bilingual files for all configured pairs.

    Note: Subtitles are assumed already synchronized (Bazarr does sync).

    Args:
        video_path: Path to video file
        sub_paths: Dict {lang: path} of source subtitles
        settings: Settings to get pairs and styles

    Returns:
        List of created .ass files

    Raises:
        ProcessingError: If processing fails
    """
    settings = settings or get_settings()
    created_files: list[Path] = []

    merge_config = MergeConfig(
        color_bottom=settings.color_bottom,
        color_top=settings.color_top,
        fontsize=settings.fontsize,
        layout=settings.layout,
    )

    try:
        for lang_bottom, lang_top in settings.pairs:
            output_path = get_output_path(video_path, lang_bottom, lang_top)

            logger.info(f"Merging {lang_bottom}-{lang_top}...")
            merge_bilingual(
                sub_paths[lang_bottom],
                sub_paths[lang_top],
                output_path,
                merge_config,
            )
            created_files.append(output_path)
            logger.info(f"Created: {output_path}")

    except Exception as e:
        # Cleanup partially created files
        for f in created_files:
            f.unlink(missing_ok=True)
        raise ProcessingError(f"Error during processing: {e}") from e

    return created_files


def process_hook(
    video_path: Path,
    subtitle_path: Path,
    lang: str,
    settings: SubtoolsSettings | None = None,
) -> HookResult:
    """Main hook entry point.

    Args:
        video_path: Path to video file
        subtitle_path: Path to downloaded subtitle (for logging)
        lang: Language code of downloaded subtitle
        settings: Settings for configuration

    Returns:
        HookResult with status and details

    Raises:
        InvalidLanguageError: If language is invalid
        ProcessingError: If processing fails
    """
    settings = settings or get_settings()

    # Validate language
    lang = validate_lang(lang, settings)
    logger.info(f"Hook called: video={video_path}, subtitle={subtitle_path.name}, lang={lang}")

    # Check that video exists
    if not video_path.exists():
        raise ProcessingError(f"Video file not found: {video_path}")

    # Acquire lock
    lock_path = get_lock_path(video_path)
    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT)

    try:
        with lock.acquire(timeout=LOCK_TIMEOUT):
            logger.debug(f"Lock acquired: {lock_path}")

            # Check if all languages are present
            sub_paths = check_all_languages_present(video_path, settings)

            if sub_paths is None:
                present, missing = get_present_and_missing(video_path, settings)
                logger.info(f"Missing languages: {missing}")

                # Start background polling for missing languages
                start_polling(video_path, settings)
                logger.info(
                    f"Background polling started for {video_path.name}, "
                    f"will retry every {settings.poll_interval}s"
                )

                return HookResult(
                    status="polling",
                    present=present,
                    missing=missing,
                    reason=f"Polling every {settings.poll_interval}s until all languages available",
                )

            # Check if we can skip
            if should_skip_existing(video_path, sub_paths, settings):
                return HookResult(
                    status="skipped",
                    reason="already_exists",
                )

            # Cancel any active polling since we're merging now
            _cancel_polling(video_path)

            # Process (no sync - Bazarr already did it)
            created_files = process_bilingual_merge(video_path, sub_paths, settings)

            return HookResult(
                status="merged",
                files=[str(f) for f in created_files],
            )

    except Timeout:
        logger.warning(f"Lock timeout for {video_path}")
        return HookResult(status="already_processing")
    finally:
        # Clean up lock file after use
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass  # Ignore if file is still in use


def get_active_polls() -> list[str]:
    """Return list of video paths currently being polled."""
    return list(_polling_jobs.keys())
