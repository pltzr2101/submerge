# Submerge v1.1.0 - Web UI & Reliable Bazarr Integration

## New Features

### Web UI (`/` and `/settings`)
- Full dark-mode dashboard showing all media files with subtitle status
- Per-video: DE/KO presence, merge status (✅/❌ indicators)
- Filter by status: All, Not Merged, Merged, Missing Languages, Error
- Manual Merge button per video row
- Sync buttons (↻DE, ↻KO) per subtitle file
- "Merge All Missing" batch button
- Settings page with runtime configuration overrides (in-memory)
- Live log viewer via SSE (Server-Sent Events)

### API Endpoints
- `GET /` — Dashboard HTML
- `GET /settings` — Settings HTML
- `GET /api/media` — JSON list of all videos with subtitle status
- `POST /api/merge` — Trigger merge for a single video
- `POST /api/sync` — Trigger subtitle sync (sub-to-sub or audio)
- `POST /scan` — Scan all directories and merge missing
- `GET /logs/stream` — SSE log stream
- `GET /api/polls` — List active polling jobs
- `POST /api/settings` — Runtime settings override

## Bug Fixes

### Hook Reliability (Race Condition Fix)
- **Polling fallback**: When `/hook` fires but not all languages are present,
  a background thread now polls every 60s (configurable via `SUBTOOLS_POLL_INTERVAL`)
  for the missing languages and triggers the merge when they appear
- **Robust filename matching**: `find_subtitle_path` now handles both 2-letter
  (ISO 639-1) and 3-letter (ISO 639-2/T) language codes
  (e.g., `de` ↔ `deu`, `ko` ↔ `kor`)

### Hook Status Change
- Previously returned `"waiting"` when languages were missing
- Now returns `"polling"` and starts background retry automatically
- Breaks no Bazarr API contract (Bazarr only cares about `"merged"` status)

## New Configuration

- `SUBTOOLS_MEDIA_ROOT` — Root directory for media scanning (default: `/data`)
- `SUBTOOLS_POLL_INTERVAL` — Polling interval in seconds (default: `60`)

## New Files

- `src/submerge/scanner.py` — Media directory scanner
- `src/submerge/templates/base.html` — Base Jinja2 template
- `src/submerge/templates/index.html` — Dashboard template
- `src/submerge/templates/settings.html` — Settings template
- `src/submerge/static/style.css` — Dark-mode CSS
- `src/submerge/static/app.js` — Shared JavaScript
- `tests/unit/test_scanner.py` — Scanner unit tests

## Modified Files

- `src/submerge/api.py` — Added UI routes, API endpoints, SSE logging
- `src/submerge/hook.py` — Added polling fallback, 3-letter code matching
- `src/submerge/config.py` — Added `media_root` and `poll_interval` settings
- `pyproject.toml` — Added `jinja2>=3.0`, `aiofiles>=23.0` dependencies
- `tests/integration/test_integration.py` — Updated for `"polling"` status

## Compatibility

- Python 3.10+ maintained
- Existing `/hook` and `/health` endpoints unchanged (Bazarr compatible)
- No breaking changes to CLI or merge functionality
- No Node.js build step required — pure Python deployment
