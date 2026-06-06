# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.3.1] - 2026-06-06

### Added

- **Bottom-dedup by top-coverage** (`_deduplicate_bottom_by_top_coverage` in
  `merge.py`): When two same-language subtitle sources are merged as `sub1`,
  near-duplicate bottom events (minimal timing variance) that cover the same
  top event are automatically removed. The bottom event with the greatest
  temporal overlap per top event is kept. Legitimate sequential 2-liners
  (mutual overlap ≤ 0 ms) are never affected.

### Fixed

- `_execute_scheduled_merge()` lock guard no longer crashes with `NoneType`
  when `_schedule_merge_lock` is uninitialised (regression from v2.3.0
  scheduler refactor).

## [2.3.0] - 2026-06-05

### Added

- **Post-merge quality checks** (`run_quality_checks` in `merge.py`):
  OVERLAP_BOTTOM, SUSPICIOUS_RATIO, LOW_COVERAGE, and EMPTY_TRACK checks
  run automatically after each merge. Warnings are returned via
  `/api/merge` response as `quality_warnings` and logged.
- **Batch export endpoint** (`GET /api/history/export?ids=1,2,3`):
  Download output files from completed merge entries as a ZIP archive.
  UI integration in history page with checkboxes and "Export Selected" button.
- **Statistics endpoint** (`GET /api/stats`): Returns aggregate merge
  statistics (merged, failed, pending counts, success rate, avg retries,
  oldest pending age). Dashboard widget on the home page.
- **Larger subtitle preview** — increased from 20 to 50 lines with
  improved modal scrolling (85vh max-height).

### Changed

- `merge_bilingual()` now returns `tuple[Path, list[QualityWarning]]`
  instead of `Path`. All callers updated (CLI, hook, queue worker,
  merge API, scanner).
- `process_bilingual_merge()` now returns `tuple[list[Path], list[QualityWarning]]`
  instead of `list[Path]`.

### Internal

- `get_history_entries_by_ids()` added to `queue.py` for batch export support.
- `get_stats()` added to `queue.py` for aggregate statistics.
- Stats router (`routers/stats.py`) registered in `api.py`.
- CSS for stats dashboard widget cards (`.stats-section`, `.stat-card`, etc.).

## [2.2.0] - 2026-06-05

### Added

- Favicon (ICO + PNG) for browser tab
- Detailed error messages in merge status banner with manual dismiss button
- Per-file error reasons in batch re-merge UI

### Fixed

- SSE logs from `run_in_executor` threads now visible in web UI via cached
  main event loop and `call_soon_threadsafe`
- EUC-KR / CP949 encoding fallback for Korean subtitle files that fail UTF-8
  and `charset-normalizer` auto-detection

### Changed

- Favicon optimized from 708 KB to 1.2 KB (favicon.ico: 1.9 KB)
- `showStatus()` auto-hides errors after 20 s instead of 5 s
- `.gitattributes` marks binary fixture and asset files to prevent Git
  conversion

### Internal

- Encoding test fixtures are programmatically generated as true binary files
  (not UTF-8) with `_assert_not_utf8` sanity guards
- `scripts/optimize_favicon.py` dev tool for reproducible favicon generation

## [2.1.0] - 2026-06-02

### Changed
- Updated default values: fontsize 22px, margin_v 20, stacked_gap 40
- Aligned bottom_fontsize/top_fontsize defaults to 22
- Moved local imports to module level in api.py
- HTTP 422 on validation errors in /api/settings
- api_frame_extract converted to async (non-blocking)
- Thread-safe polling jobs with settings_fn callback
- CJK wrap_style=0 for correct line breaking
- charset-normalizer fallback for encoding detection
- ruff format and coverage checks added to CI
- .env.example with all SUBTOOLS_* variables
- Complete README styling table with all variables

## [2.0.3] - 2026-06-01

### Fixed

- `asyncio.get_event_loop()` replaced with `asyncio.get_running_loop()` in
  `api_merge` (deprecated in Python 3.10+, DeprecationWarning in 3.12+)
- `api_queue_retry` converted to `async def` with `run_in_threadpool` to
  prevent blocking uvicorn worker on large subtitle files
- `BackgroundTask` lambda closure replaced with direct `Path.unlink` method
  reference for safer variable binding at construction time

## [2.0.2] - 2026-06-01

### Fixed

- `asyncio.Queue` lazy-initialized via `_get_log_queue()` to avoid creation
  outside event loop (fatal in Python 3.12+)
- `FileResponse(background=lambda)` replaced with `BackgroundTask(lambda)`
  so temporary frame-extract files are actually deleted on download
- `api_merge` runs `process_bilingual_merge` in `loop.run_in_executor()` to
  avoid blocking the uvicorn worker during long merge operations
- `create_app()` no longer raises `RuntimeError` on missing `SUBTOOLS_PAIRS`;
  server starts gracefully, logs a warning, and `/hook` returns HTTP 503
- `/health` now includes `configured` boolean and `pairs` list
- `bottom_color`, `top_color`, `bottom_outline_color`, `top_outline_color`
  now validated by hex color validator (were previously unvalidated)
- Removed dead code: `_PRESETS_DIR = Path('/data/style_presets')`
- `docker-compose.example.yml` updated for `de-ko` with all new env vars

## [2.0.1] - 2026-06-01

### Fixed

- **Race condition (queue vs. polling):** Queue worker now skips videos that
  are actively being polled by the webhook handler, preventing duplicate merges
- **Style field handling:** Fixed style field mapping so `bottom_color` /
  `top_color` and other granular style settings are correctly read from
  environment variables and applied to ASS output
- **Sync path lookup:** Fixed subtitle file path resolution in sync endpoints
  to correctly handle files within the media root directory structure
- **Tempfile leak:** Resolved a bug where temporary files created during the
  merge process were not always cleaned up on error paths
- **Scan blocking:** `/scan` endpoint converted to async with background task
  dispatch so it no longer blocks the request handler for large directories
- **Missing auth on UI pages:** Basic auth middleware now correctly protects
  all UI pages including `/styles`
- **Version reporting:** Fixed `__version__` export and added to `/health`
- **Config:** `rate_limit_rpm`, `ui_user`, and `ui_password` fields added to
  settings model with environment variable bindings

## [2.0.0] - 2026-06-01

### Added

- Web-UI (FastAPI + Jinja2): Dashboard, Settings, Style Editor
- `/api/media` — JSON overview of all videos with subtitle status
- `/api/merge` — manual merge trigger via HTTP
- `/api/sync` — subtitle synchronization via ffsubsync
- `/api/queue` + `/api/queue/{id}/retry` + `/api/queue/{id}/remove`
- `/scan` — background scan of all media directories
- `/logs/stream` — SSE live log stream
- `/lingarr-hook` — separate webhook endpoint for Lingarr
- SQLite retry queue with background worker for failed merges
- Polling fallback: automatic retries when subtitles are still missing
- Style presets (Standard, Cinema Dark, Bright) + persistent custom presets
- Basic Auth for Web-UI (`SUBTOOLS_UI_USER` / `SUBTOOLS_UI_PASSWORD`)
- Rate limiting (`SUBTOOLS_RATE_LIMIT_RPM`)
- `scanner.py` — media directory scanner
- `queue.py` — SQLite queue worker
- `langmap.py` — ISO 639-1/2 language code mapping

### Changed

- Version 1.0.0 → 2.0.0
- FastAPI app uses `lifespan` context manager instead of deprecated `@app.on_event`
- `/health` response now returns `configured` and `pairs` fields

[2.3.1]: https://github.com/pltzr2101/submerge/compare/v2.3.0...v2.3.1
[2.3.0]: https://github.com/pltzr2101/submerge/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/pltzr2101/submerge/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/pltzr2101/submerge/compare/v2.0.3...v2.1.0
[2.0.3]: https://github.com/pltzr2101/submerge/compare/v2.0.2...v2.0.3
[2.0.2]: https://github.com/pltzr2101/submerge/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/pltzr2101/submerge/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/pltzr2101/submerge/releases/tag/v2.0.0
