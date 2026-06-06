"""Business logic for Bazarr hook for automatic bilingual subtitle generation."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pysubs2
from filelock import FileLock, Timeout

from .config import SubtoolsSettings, get_settings
from .langmap import get_all_aliases, normalize_lang
from .merge import MergeConfig, merge_bilingual
from .models import HookResult, InvalidLanguageError, ProcessingError

logger = logging.getLogger(__name__)

# Constants
LOCK_TIMEOUT = 5  # seconds


def find_video_for_subtitle(sub_path: Path) -> Path | None:
    """Find the video file corresponding to a subtitle file.

    Peels language-code suffixes from the filename stem until a
    matching video file is found. Handles multi-dot filenames like
    'Movie.2024.BluRay.de.hi.srt'.

    Args:
        sub_path: Path to subtitle file

    Returns:
        Path to video file or None
    """
    video_exts = (".mkv", ".mp4", ".avi", ".m4v")
    stem = sub_path.stem

    # Keep peeling suffixes until find a video or no dots left.
    # Check each stem, including the final dot-free form, inside the loop.
    while True:
        for ext in video_exts:
            candidate = sub_path.parent / (stem + ext)
            if candidate.exists():
                return candidate
        if "." not in stem:
            break
        stem = stem.rsplit(".", 1)[0]

    return None


def _config_fingerprint(settings: SubtoolsSettings) -> str:
    """Return a short SHA-256 fingerprint of all style-relevant settings.

    Used to detect config changes that require a re-merge even when source
    mtime hasn't changed.
    """
    style_fields = {
        "bottom_color": settings.bottom_color,
        "top_color": settings.top_color,
        "bottom_fontsize": settings.bottom_fontsize,
        "top_fontsize": settings.top_fontsize,
        "font_bottom": settings.font_bottom,
        "font_top": settings.font_top,
        "bottom_bold": settings.bottom_bold,
        "top_bold": settings.top_bold,
        "bottom_outline": settings.bottom_outline,
        "top_outline": settings.top_outline,
        "bottom_outline_color": settings.bottom_outline_color,
        "top_outline_color": settings.top_outline_color,
        "bottom_shadow": settings.bottom_shadow,
        "top_shadow": settings.top_shadow,
        "bottom_margin_v": settings.bottom_margin_v,
        "top_margin_v": settings.top_margin_v,
        "bottom_margin_h": settings.bottom_margin_h,
        "top_margin_h": settings.top_margin_h,
        "bottom_spacing": settings.bottom_spacing,
        "top_spacing": settings.top_spacing,
        "stacked_gap": settings.stacked_gap,
        "layout": settings.layout,
    }
    serialized = json.dumps(style_fields, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# Track active polling jobs: video_path -> threading.Event (set to cancel)
# Protected by _polling_jobs_lock for thread safety.
_polling_jobs: dict[str, threading.Event] = {}
_polling_jobs_lock: threading.Lock = threading.Lock()


# ---------------------------------------------------------------------------
# Pure functions (no dependency on queue.py) — must be defined before the
# queue import below because queue.py imports some of them.
# ---------------------------------------------------------------------------


def validate_lang(lang: str, settings: SubtoolsSettings | None = None) -> str:
    """Validate and normalize a language.

    Normalizes any language code variant (ISO 639-1, ISO 639-2, locale-style)
    to the standard 2-letter code and validates it's in the configured pairs.

    Args:
        lang: Language code (e.g., fr, fra, fr-FR)
        settings: Settings to get required languages

    Returns:
        Normalized ISO 639-1 lowercase language code

    Raises:
        InvalidLanguageError: If language is not in configured pairs
    """
    settings = settings or get_settings()
    normalized = normalize_lang(lang)
    if normalized is None:
        raise InvalidLanguageError(f"Unrecognized language: {lang}. Expected ISO 639-1 code.")
    if normalized not in settings.required_langs:
        raise InvalidLanguageError(
            f"Invalid language: {lang} (normalized: {normalized}). "
            f"Must be one of: {', '.join(sorted(settings.required_langs))}"
        )
    return normalized


def _get_lang_patterns(video_stem: str, lang: str) -> list[str]:
    """Build all possible filename patterns for a language code.

    Handles 2-letter (ISO 639-1), 3-letter (ISO 639-2), and locale-style
    codes that Bazarr/Lingarr might use (e.g., de, deu, ger, de-DE).

    Normal tracks (.srt) are listed first; SDH/HI/CC/forced
    variants are listed after all normal patterns so that
    ``find_subtitle_path`` picks the regular track when both exist.

    .ass files are intentionally excluded because submerge generates
    .ass output files — a previously merged ``Movie.de-ko.ass`` must
    never be picked up as a subtitle input source.

    Args:
        video_stem: Video filename stem (without extension)
        lang: ISO 639-1 2-letter language code

    Returns:
        List of filename patterns to try, in priority order
    """
    aliases = get_all_aliases(lang)
    extensions = [".srt"]
    # SDH (Subtitles for Deaf/Hard-of-hearing), HI (Hearing Impaired),
    # CC (Closed Captions), forced (Forced Narrative) — all are
    # deprioritised variants that should only be used when no normal
    # subtitle track exists.  .ass versions are excluded for the same
    # reason as above.
    variant_extensions = [
        ".hi.srt",
        ".sdh.srt",
        ".cc.srt",
        ".forced.srt",
    ]

    patterns: list[str] = []
    seen: set[str] = set()

    for alias in aliases:
        for ext in extensions:
            p = f"{video_stem}.{alias}{ext}"
            if p not in seen:
                patterns.append(p)
                seen.add(p)

    for alias in aliases:
        for ext in variant_extensions:
            p = f"{video_stem}.{alias}{ext}"
            if p not in seen:
                patterns.append(p)
                seen.add(p)

    return patterns


def find_subtitle_path(video_path: Path, lang: str) -> Path | None:
    """Find subtitle file for a language.

    Searches in priority order:
    1. Normal tracks (.srt) for any known alias
    2. SDH/HI/CC/forced variants (.hi.srt, .sdh.srt, .cc.srt, .forced.srt)
       only when no normal track exists

    .ass files are intentionally excluded from subtitle search because
    submerge generates .ass output files that must never be picked up
    as input sources.

    Also tries 3-letter ISO 639-2 codes (e.g., 'deu' for 'de').
    Falls back to a case-insensitive directory scan so that files named
    ``Movie.de.HI.srt`` or ``Movie.DE.SDH.srt`` are still found.

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

    # Case-insensitive fallback: scan directory for files matching
    # ``{video_stem}.*`` and classify them by suffix priority.
    aliases = get_all_aliases(lang)
    alias_set = {a.lower() for a in aliases}
    variant_suffixes = {"hi", "sdh", "cc", "forced"}

    candidates: list[tuple[int, Path]] = []
    try:
        for entry in video_dir.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if not name.lower().startswith(video_stem.lower() + "."):
                continue
            # Skip .ass output files (submerge's own output)
            if name.lower().endswith(".ass"):
                continue
            # Extract the part after the video stem
            rest = name[len(video_stem) + 1 :].lower()
            # Split into parts: e.g., "de.hi.srt" -> ["de", "hi", "srt"]
            parts = rest.rsplit(".", 2)
            if len(parts) < 2:
                continue
            # Check if any part is a known alias
            alias_match = any(p in alias_set for p in parts)
            if not alias_match:
                continue
            # File is relevant — classify as normal or variant
            has_variant = any(p in variant_suffixes for p in parts)
            candidates.append((0 if not has_variant else 1, Path(entry)))
    except OSError:
        pass

    if candidates:
        candidates.sort(key=lambda x: x[0])
        best = candidates[0][1]
        logger.debug(f"Found (case-insensitive fallback): {best}")
        return best

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
    for lang in settings.required_langs:
        if find_subtitle_path(video_path, lang):
            present.append(lang)
        else:
            missing.append(lang)
    return present, missing


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

    Compares both source mtime and a config fingerprint stored in the
    .ass file.  If the fingerprint has changed (e.g. fontsize, colors,
    layout) a re-merge is forced even when source mtime is unchanged.

    Args:
        video_path: Path to video file
        sub_paths: Dict {lang: path} of source subtitles
        settings: Settings to get configured pairs

    Returns:
        True if all .ass exist and are newer than their sources
    """
    settings = settings or get_settings()
    current_fingerprint = _config_fingerprint(settings)

    for lang_bottom, lang_top in settings.pairs:
        output_path = get_output_path(video_path, lang_bottom, lang_top)

        if not output_path.exists():
            logger.debug(f"Skip check: {output_path} does not exist")
            return False

        output_mtime = output_path.stat().st_mtime

        # Check config fingerprint — force re-merge if style config changed
        try:
            stored_subs = pysubs2.load(str(output_path))
            stored_fingerprint = stored_subs.info.get("SubmergeConfigHash", "")
        except Exception:
            stored_fingerprint = ""

        if stored_fingerprint != current_fingerprint:
            logger.debug(
                f"Skip check: config fingerprint changed for {output_path.name} "
                f"(stored={stored_fingerprint!r}, current={current_fingerprint!r})"
            )
            return False

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


# ---------------------------------------------------------------------------
# Import from queue — safe now because all names queue.py needs from this
# module are already defined above.
# ---------------------------------------------------------------------------

from .queue import dequeue, enqueue  # noqa: E402

# ---------------------------------------------------------------------------
# Functions that depend on queue (defined after the import above)
# ---------------------------------------------------------------------------


def cancel_polling(video_path: Path) -> None:
    """Cancel any active polling for a video."""
    key = str(video_path.resolve())
    with _polling_jobs_lock:
        event = _polling_jobs.pop(key, None)
    if event is not None:
        event.set()
        logger.info(f"Polling cancelled for {video_path.name}")


def _polling_worker(
    video_path: Path,
    settings: SubtoolsSettings,
    cancel_event: threading.Event,
    settings_fn: Callable[[], SubtoolsSettings] | None = None,
) -> None:
    """Background worker that polls for missing languages and triggers merge.

    Args:
        video_path: Path to video file
        settings: Fallback configuration (used if settings_fn is None)
        cancel_event: Set to stop polling
        settings_fn: Optional callable returning current effective settings
    """
    effective = settings_fn() if settings_fn else settings
    poll_interval = effective.poll_interval
    max_attempts = max(1, int(effective.retry_timeout_h * 3600 / max(poll_interval, 1)))
    max_attempts = min(max_attempts, 500)  # Safety cap

    logger.info(
        f"Polling started for {video_path.name} "
        f"(interval={poll_interval}s, max={max_attempts} attempts, ~{effective.retry_timeout_h}h)"
    )

    key = str(video_path.resolve())
    try:
        for attempt in range(1, max_attempts + 1):
            current_settings = settings_fn() if settings_fn else settings
            current_interval = current_settings.poll_interval
            if cancel_event.wait(timeout=current_interval):
                logger.info(f"Polling cancelled for {video_path.name} after {attempt} attempts")
                return

            try:
                sub_paths = check_all_languages_present(video_path, current_settings)
                if sub_paths is not None:
                    logger.info(f"All languages present for {video_path.name} (attempt {attempt})")

                    if should_skip_existing(video_path, sub_paths, current_settings):
                        logger.info(f"Skipping {video_path.name}: outputs already up-to-date")
                        return

                    lock_path = get_lock_path(video_path, current_settings)
                    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT)
                    try:
                        with lock.acquire(timeout=LOCK_TIMEOUT):
                            # Re-check after acquiring lock (another process may have merged)
                            if should_skip_existing(video_path, sub_paths, current_settings):
                                logger.info(
                                    f"Skipping {video_path.name}:"
                                    " outputs up-to-date (post-lock check)"
                                )
                                return
                            created_files, _ = process_bilingual_merge(
                                video_path, sub_paths, current_settings
                            )
                    except Timeout:
                        logger.warning(
                            f"Polling worker: lock timeout for {video_path.name}, "
                            "will retry next cycle"
                        )
                        continue

                    logger.info(
                        f"Polling merge complete for {video_path.name}: "
                        f"{[f.name for f in created_files]}"
                    )
                    # Mark queue entry as done
                    dequeue(video_path, "done", settings=current_settings)
                    return

                present, missing = get_present_and_missing(video_path, current_settings)
                logger.debug(
                    f"Polling {video_path.name} (attempt {attempt}/{max_attempts}): "
                    f"present={present}, missing={missing}"
                )
            except Exception as e:
                logger.error(f"Polling error for {video_path.name} (attempt {attempt}): {e}")

        logger.warning(f"Polling exhausted for {video_path.name} after {max_attempts} attempts")
    finally:
        with _polling_jobs_lock:
            _polling_jobs.pop(key, None)


def start_polling(
    video_path: Path,
    settings: SubtoolsSettings | None = None,
    settings_fn: Callable[[], SubtoolsSettings] | None = None,
) -> bool:
    """Start a background polling job for a video.

    If a polling job already exists for this video, it's cancelled first.

    Args:
        video_path: Path to video file
        settings: Configuration (fallback)
        settings_fn: Optional callable returning current effective settings

    Returns:
        True if polling was started
    """
    settings = settings or get_settings()

    # Cancel any existing polling for this file
    cancel_polling(video_path)

    key = str(video_path.resolve())
    cancel_event = threading.Event()
    with _polling_jobs_lock:
        _polling_jobs[key] = cancel_event

    thread = threading.Thread(
        target=_polling_worker,
        args=(video_path, settings, cancel_event, settings_fn),
        daemon=True,
        name=f"submerge-poll-{video_path.name}",
    )
    thread.start()
    return True


def get_lock_path(video_path: Path, settings: SubtoolsSettings | None = None) -> Path:
    """Return the lock file path for a video, stored in config_dir/locks."""
    s = settings or get_settings()
    locks_dir = Path(s.config_dir) / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    return locks_dir / f"{video_path.stem}.lock"


def process_bilingual_merge(
    video_path: Path,
    sub_paths: dict[str, Path],
    settings: SubtoolsSettings | None = None,
) -> tuple[list[Path], list[Any]]:
    """Generate bilingual files for all configured pairs.

    Note: Subtitles are assumed already synchronized (Bazarr does sync).

    Args:
        video_path: Path to video file
        sub_paths: Dict {lang: path} of source subtitles
        settings: Settings to get pairs and styles

    Returns:
        Tuple of (list of created .ass files, list of QualityWarning)

    Raises:
        ProcessingError: If processing fails
    """
    from .merge import QualityWarning

    settings = settings or get_settings()
    created_files: list[Path] = []
    all_warnings: list[QualityWarning] = []

    merge_config = MergeConfig(
        color_bottom=settings.bottom_color,
        color_top=settings.top_color,
        fontsize_bottom=settings.bottom_fontsize,
        fontsize_top=settings.top_fontsize,
        font_bottom=settings.font_bottom,
        font_top=settings.font_top,
        bold_bottom=settings.bottom_bold,
        bold_top=settings.top_bold,
        outline_bottom=settings.bottom_outline,
        outline_top=settings.top_outline,
        outline_color_bottom=settings.bottom_outline_color,
        outline_color_top=settings.top_outline_color,
        shadow_bottom=settings.bottom_shadow,
        shadow_top=settings.top_shadow,
        margin_v_bottom=settings.bottom_margin_v,
        margin_v_top=settings.top_margin_v,
        margin_h_bottom=settings.bottom_margin_h,
        margin_h_top=settings.top_margin_h,
        spacing_bottom=settings.bottom_spacing,
        spacing_top=settings.top_spacing,
        stacked_gap=settings.stacked_gap,
        layout=settings.layout,
    )
    merge_config._fingerprint = _config_fingerprint(settings)

    try:
        for lang_bottom, lang_top in settings.pairs:
            # Fail fast with explicit message if a required language is missing
            for lang in (lang_bottom, lang_top):
                if lang not in sub_paths:
                    raise ProcessingError(
                        f"Missing subtitle for language '{lang}' "
                        f"(needed for pair {lang_bottom}-{lang_top})"
                    )

            output_path = get_output_path(video_path, lang_bottom, lang_top)

            # Enforce order from pairs config: first language (lang_bottom)
            # always goes to sub1_path (displayed at bottom), second
            # language (lang_top) always goes to sub2_path (displayed at top).
            sub1_path = sub_paths[lang_bottom]
            sub2_path = sub_paths[lang_top]

            logger.info(f"Merging {lang_bottom}-{lang_top}...")
            _, pair_warnings = merge_bilingual(sub1_path, sub2_path, output_path, merge_config)
            all_warnings.extend(pair_warnings)
            created_files.append(output_path)
            logger.info(f"Created: {output_path}")

    except Exception as e:
        # Cleanup partially created files
        for f in created_files:
            f.unlink(missing_ok=True)
        from .notify import send_notification

        send_notification(
            title="Submerge: Merge fehlgeschlagen",
            message=f"{video_path.name}: {str(e)}",
            settings=settings,
            tags=["x", "submerge"],
        )
        raise ProcessingError(f"Error during processing: {e}") from e

    from .notify import send_notification

    send_notification(
        title="Submerge: Merge abgeschlossen",
        message=f"{video_path.name} → {len(created_files)} Datei(en) erstellt",
        settings=settings,
        tags=["white_check_mark", "submerge"],
    )
    return created_files, all_warnings


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
    lock_path = get_lock_path(video_path, settings)
    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT)

    try:
        with lock.acquire(timeout=LOCK_TIMEOUT):
            logger.debug(f"Lock acquired: {lock_path}")

            # Check if all languages are present
            sub_paths = check_all_languages_present(video_path, settings)

            if sub_paths is None:
                present, missing = get_present_and_missing(video_path, settings)
                logger.info(f"Missing languages: {missing}")

                # Persist to SQLite queue for background retry
                enqueue(video_path, settings)

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
            cancel_polling(video_path)

            # Mark queue entry as done
            dequeue(video_path, "done", settings=settings)

            # Process (no sync - Bazarr already did it)
            created_files, _ = process_bilingual_merge(video_path, sub_paths, settings)

            return HookResult(
                status="merged",
                files=[str(f) for f in created_files],
            )

    except Timeout:
        logger.info(f"Hook for {video_path.name}: already processing by polling worker — skipped")
        return HookResult(status="already_processing")


def get_active_polls() -> list[str]:
    """Return list of video paths currently being polled."""
    with _polling_jobs_lock:
        return list(_polling_jobs.keys())


def _get_polling_jobs() -> dict[str, threading.Event]:
    """Return a snapshot copy of the polling jobs dict (for testing)."""
    with _polling_jobs_lock:
        return dict(_polling_jobs)
