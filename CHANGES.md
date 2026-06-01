# Submerge v2.0.1 - Bug Fixes

## Fixes

- **Race condition (Fix 1):** Queue worker now skips videos actively being polled by the
  in-process polling worker. Polling worker dequeues after successful merge. Clear priority:
  polling handles active jobs, queue worker only takes what polling isn't handling.
- **MergeConfig style fields (Fix 2):** `process_bilingual_merge()` now passes all expanded
  style fields (font_bottom, font_top, bold_bottom, bold_top, outline, outline colors,
  shadow, margin, spacing, stacked_gap) to MergeConfig instead of only 4 fields.
- **api_sync path lookup (Fix 3):** Replaced broken `rsplit(".", 1)` video detection with
  robust `_find_video_for_subtitle()` helper that peels language-code suffixes from the
  filename stem until a matching video file is found.
- **Tempfile leak (Fix 4):** `api_frame_extract` now cleans up temp files on HTTPException
  and generic Exception, not just on the success path.
- **Scan blocking (Fix 5):** `/scan` now uses FastAPI `BackgroundTasks` so the request
  thread returns immediately; scan progress is visible via `/logs/stream`.
- **Version mismatch (Fix 6):** FastAPI app version now reads from `__version__` instead of
  hardcoded `"1.0.0"`. Removed unused `get_lock_path` import in `api_merge`.
- **BasicAuth username (Fix 7):** New `SUBTOOLS_UI_USER` env var (default `"admin"`). Both
  username and password are now validated in BasicAuth.
- **Path traversal (Fix 8):** `validate_path()` now accepts `check_media_root=True` to
  enforce that resolved paths are within `SUBTOOLS_MEDIA_ROOT`. Hooks remain unaffected.

## Tests

- Added 4 tests (2 for Fix 1, 2 for Fix 2)
- Total: 98 passing

## v2.0.0 - Web UI & Production Stack

- Web UI Dashboard, Style Editor, Settings, Log Viewer
- Lingarr webhook, SQLite retry queue, ISO 639 langmap
- Style presets, frame extraction, Basic Auth, rate limiting
- Expanded per-language config, full ASS style control

## v1.1.0 - Web UI & Reliable Bazarr Integration

- Web UI dashboard + settings + log viewer
- Polling fallback for missing languages
- Robust 2/3-letter ISO code matching

## v1.0.0 - Initial Release

- CLI: merge, sync, extract, list-tracks
- Bazarr /hook webhook
- SRT to bilingual ASS
