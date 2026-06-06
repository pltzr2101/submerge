# Submerge
> Originally inspired by [b4stOss/submerge](https://github.com/b4stOss/submerge).

Automatic bilingual subtitle merge service for ARR stacks. Combines two single-language subtitles into a professionally styled bilingual ASS file — with Web UI, Bazarr/Lingarr integration, live style preview, and background retry queue.

[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/github/v/release/pltzr2101/submerge?label=version)](https://github.com/pltzr2101/submerge/releases/latest)
[![Tests](https://img.shields.io/badge/tests-100%20passing-success)](tests/)

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Bazarr Integration](#bazarr-integration)
- [API Reference](#api-reference)
- [Web UI](#web-ui)
- [Development](#development)
- [License](#license)

## Features

- **Automatic bilingual merge** — e.g. `de.srt` + `ko.srt` → `de-ko.ass` with distinct positioning (one language at bottom, one at top, or stacked)
- **Bazarr & Lingarr webhook integration** with polling fallback when subtitles are still downloading/translating
- **Web UI**: media overview dashboard, manual merge/sync buttons, queue management, live SSE log stream
- **Style presets** — 3 built-in (Standard, Cinema Dark, Bright) + create and save your own via the UI
- **Post-merge quality checks** — automatic detection of overlapping tracks, line count imbalances, low coverage, and empty tracks
- **Bottom-dedup by top-coverage** — automatically removes near-duplicate bottom events when two same-language sources are merged
- **Statistics dashboard** — merge success rate, pending/failed counts, average retries on the home page
- **Batch export** — download output files from multiple history entries as a ZIP archive
- **Basic Auth + rate limiting** for Web UI protection
- **ffsubsync integration** for subtitle synchronization (sub-to-sub or audio-based)
- **Docker** — single-container deployment with healthcheck
- **CLI** — `merge`, `sync`, `extract`, `list-tracks`, `serve`

## Prerequisites

- **Docker + Docker Compose** (recommended) — or Python 3.10+ with ffmpeg for manual install
- **Bazarr + Lingarr** for full automation (Submerge also works standalone via CLI or Web UI)

## Quick Start

Add `submerge` to your existing ARR stack `docker-compose.yml`:

```yaml
services:
  submerge:
    image: ghcr.io/pltzr2101/submerge:2.0.3
    # Alternatively build locally:
    # build: .
    container_name: submerge
    restart: unless-stopped
    ports:
      - "8282:8282"
    volumes:
      # IMPORTANT: must match the path Bazarr/Sonarr/Radarr use
      - /path/to/media:/data
    environment:
      # REQUIRED: Language pairs (bottom-top, comma separated)
      - SUBTOOLS_PAIRS=de-ko

      # Media root inside the container
      - SUBTOOLS_MEDIA_ROOT=/data

      # Polling interval in seconds (how often to check for missing subtitles)
      - SUBTOOLS_POLL_INTERVAL=60

      # Subtitle appearance
      - SUBTOOLS_COLOR_BOTTOM=#FFFFFF    # Bottom language color (German)
      - SUBTOOLS_COLOR_TOP=#FFD700       # Top language color (Korean, gold)
      - SUBTOOLS_BOTTOM_FONTSIZE=22     # Font size for bottom track
      - SUBTOOLS_TOP_FONTSIZE=22        # Font size for top track
      - SUBTOOLS_FONT_TOP=Noto Sans KR   # CJK font for top (Korean)
      - SUBTOOLS_LAYOUT=top-bottom       # Layout: top-bottom or stacked

      # Web UI authentication (leave empty for no password)
      - SUBTOOLS_UI_USER=admin
      - SUBTOOLS_UI_PASSWORD=

      # Rate limiting: max requests per minute per IP (0 = disabled)
      - SUBTOOLS_RATE_LIMIT_RPM=30
```

Then:
```bash
docker compose up -d submerge
```

Open `http://<server-ip>:8282` for the Web UI.

For standalone CLI usage:
```bash
pip install -e .
submerge merge de.srt ko.srt -o bilingual.ass
```

## Configuration

### Required

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBTOOLS_PAIRS` | — | Language pairs (bottom-top), comma-separated. Example: `de-ko,en-ko` |

### Styling

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBTOOLS_COLOR_BOTTOM` | `#FFFFFF` | Bottom subtitle text color (hex) |
| `SUBTOOLS_COLOR_TOP` | `#FFD700` | Top subtitle text color (hex) |
| `SUBTOOLS_BOTTOM_FONTSIZE` | `22` | Font size for bottom track |
| `SUBTOOLS_TOP_FONTSIZE` | `22` | Font size for top track |
| `SUBTOOLS_FONTSIZE` | `18` | **Legacy** — sets both tracks. Prefer `BOTTOM_FONTSIZE`/`TOP_FONTSIZE` |
| `SUBTOOLS_LAYOUT` | `top-bottom` | Layout: `top-bottom` or `stacked` (both at bottom) |
| `SUBTOOLS_BOTTOM_OUTLINE` | `2.0` | Bottom text outline width |
| `SUBTOOLS_TOP_OUTLINE` | `2.0` | Top text outline width |
| `SUBTOOLS_BOTTOM_OUTLINE_COLOR` | `#000000` | Bottom text outline color |
| `SUBTOOLS_TOP_OUTLINE_COLOR` | `#000000` | Top text outline color |
| `SUBTOOLS_BOTTOM_SHADOW` | `1.0` | Bottom text shadow depth (0 = disabled) |
| `SUBTOOLS_TOP_SHADOW` | `1.0` | Top text shadow depth (0 = disabled) |
| `SUBTOOLS_BOTTOM_BOLD` | `false` | Bold for bottom track |
| `SUBTOOLS_TOP_BOLD` | `false` | Bold for top track |
| `SUBTOOLS_BOTTOM_MARGIN_V` | `20` | Bottom track vertical margin (pixels from bottom edge) |
| `SUBTOOLS_TOP_MARGIN_V` | `20` | Top track vertical margin (pixels from top edge) |
| `SUBTOOLS_BOTTOM_MARGIN_H` | `20` | Bottom track horizontal margin |
| `SUBTOOLS_TOP_MARGIN_H` | `20` | Top track horizontal margin |
| `SUBTOOLS_BOTTOM_SPACING` | `0.0` | Bottom track letter spacing |
| `SUBTOOLS_FONT_BOTTOM` | `""` | Font name for bottom track (empty = system default). Use for non-Latin scripts. |
| `SUBTOOLS_TOP_SPACING` | `0.0` | Top track letter spacing |
| `SUBTOOLS_STACKED_GAP` | `40` | Gap between stacked subtitle lines (only when `LAYOUT=stacked`) |

### Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBTOOLS_MEDIA_ROOT` | `/data` | Root directory for media files (container path) |
| `SUBTOOLS_POLL_INTERVAL` | `60` | Seconds between background retry checks for missing subtitles |
| `SUBTOOLS_RETRY_TIMEOUT_H` | `48` | Abandon pending merges after this many hours |
| `SUBTOOLS_CONFIG_DIR` | `/config` | Directory for persisted config (presets, app settings, queue DB). **Must be mapped to a volume for persistence.** |
| `SUBTOOLS_UI_USER` | `admin` | Username for Web UI basic auth |
| `SUBTOOLS_UI_PASSWORD` | `""` | Password for Web UI basic auth (empty = no authentication) |
| `SUBTOOLS_RATE_LIMIT_RPM` | `30` | Max requests per minute per IP (0 = disabled) |

## Bazarr Integration

Configure Bazarr to call Submerge after each subtitle download:

1. In Bazarr: **Settings → Subtitles → Post-Processing**, enable post-processing.
2. Configure Bazarr to POST to `http://submerge:8282/hook` with form fields:
   - `video={video}` — path to the video file
   - `subtitle={subtitle}` — path to the downloaded subtitle
   - `lang={lang}` — ISO 639-1 language code (e.g. `de`, `ko`, `en`)

Example `curl` equivalent:
```bash
curl -X POST "http://submerge:8282/hook" \
  --data-urlencode "video=/data/media/movie.mkv" \
  --data-urlencode "subtitle=/data/media/movie.de.srt" \
  --data-urlencode "lang=de"
```

If the other language is still missing (e.g. Korean wasn't downloaded yet), Submerge starts background polling and retries automatically.

### Lingarr

Configure Lingarr to call Submerge after each translation completes:
```
POST http://submerge:8282/lingarr-hook
```
Same POST format as the Bazarr hook — form fields `video`, `subtitle`, `lang`. This triggers a fresh check; if all languages are now present, the merge runs immediately.

## API Reference

### Webhooks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/hook` | Bazarr post-processing webhook |
| `POST` | `/lingarr-hook` | Lingarr post-processing webhook |
| `GET` | `/health` | Health check (ffmpeg, ffprobe, config status) |

### Media & Merge

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/media` | List all media with subtitle status (JSON) |
| `POST` | `/api/merge` | Trigger merge for one video (`{"video_path": "..."}`) |
| `POST` | `/api/batch-merge` | Trigger merge for multiple videos. Body: `{"video_paths": ["<path>", ...], "template": "<preset>", "overwrite": true}`. Response: `{"results": [{"video": "<name>", "status": "merged"|"skipped"|"error"|"polling", ...}]}` |
| `POST` | `/api/sync` | Synchronize a subtitle file via ffsubsync |
| `POST` | `/scan` | Scan all directories, start missing merges |
| `GET` | `/api/polls` | List active background polling jobs |

### Queue

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/queue` | List all pending queue entries |
| `POST` | `/api/queue/{id}/retry` | Retry a specific queue entry now |
| `POST` | `/api/queue/{id}/remove` | Remove a queue entry |
| `GET` | `/api/history` | List completed merge history (done + failed), newest first. Query: `?limit=200` |
| `POST` | `/api/history/clear` | Delete all completed entries from history |
| `GET` | `/api/history/export` | Download output files for given entry IDs as ZIP. Query: `?ids=1,2,3` (max 50) |
| `GET` | `/api/stats` | Aggregate merge statistics (merged, failed, pending, success rate, avg retries) |

### Style Presets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/presets` | List all available presets |
| `GET` | `/api/presets/{name}` | Get styles for a specific preset |
| `POST` | `/api/presets` | Save a custom preset |
| `DELETE` | `/api/presets/{name}` | Delete a custom preset |
| `GET` | `/api/frame-extract` | Extract a video frame as JPEG (query: `video_path`, `timestamp_s`) |

### Scheduler & Templates

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/settings/default-template` | Get the currently active default style preset name |
| `POST` | `/api/settings/default-template` | Set the active default style preset. Body: `{"template": "<preset_name>"}` |
| `GET` | `/api/settings/schedule` | Get current auto-merge scheduler configuration. Response includes `auto_merge_enabled`, `schedule_time` (HH:MM), `run_on_startup`, `schedule_template` |
| `POST` | `/api/settings/schedule` | Configure the auto-merge scheduler. Body: `{"auto_merge_enabled": bool, "schedule_time": "HH:MM", "run_on_startup": bool, "schedule_template": "<preset_name_or_empty>"}` |
| `DELETE` | `/api/media/merged` | Delete merged .ass subtitle file for a specific video. Body: `{"video_path": "<path>"}` |

### UI & Monitoring

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI Dashboard |
| `GET` | `/styles` | Style Editor page |
| `GET` | `/settings` | Settings page |
| `GET` | `/history` | Merge history page |
| `GET` | `/logs/stream` | SSE live log stream |
| `GET` | `/api/settings` | Returns the current effective runtime settings. `notification_token` is always masked as `"***"` in the response. |
| `POST` | `/api/settings` | Update runtime settings (in-memory, not persisted) |

## Web UI

Submerge ships with a responsive dark-mode Web UI at `http://<host>:8282`:

| Page | Description |
|------|-------------|
| **Dashboard** (`/`) | Media overview table with subtitle status (DE ✓/✗, KO ✓/✗, merged ✓/✗), per-video merge/sync buttons, batch "merge all missing", search/filter, polling status badge |
| **History** (`/history`) | Merge history table showing all past merge operations with status badges, duration, output files, and timestamps. Client-side filtering by status (all / merged / failed), auto-refresh every 30 s, clear-button |
| **Settings** (`/settings`) | Override `SUBTOOLS_*` environment variables at runtime. **In-memory only — changes are lost on container restart.** To persist style changes permanently: save as a Preset in the Style Editor, then set it as the Default Template via `POST /api/settings/default-template` or the Style Editor UI. |
| **Style Editor** (`/styles`) | Two-tab editor (Bottom/Top) with color pickers, font size, outline/shadow controls, CJK font selector, canvas preview, preset save/load, ASS export button |

## Auto-Merge Scheduler

Submerge can run a daily automatic merge scan at a configured time using APScheduler.

> **Note:** APScheduler is an optional dependency. If not installed, the scheduler is silently disabled and a warning is logged.

Configure via the **Settings** page (`/settings`) or directly via the API (`POST /api/settings/schedule`):

| Field | Default | Description |
|-------|---------|-------------|
| `auto_merge_enabled` | `false` | Enable/disable the daily scheduled scan |
| `schedule_time` | `03:00` | Time of day to run (24h format, HH:MM) |
| `run_on_startup` | `false` | Run a scan immediately when the container starts |
| `schedule_template` | `""` | Style preset to use for scheduled merges (empty = active default template) |

Schedule settings are persisted to `/config/app_settings.json` and survive container restarts.

## Development

```bash
# Install with dev dependencies
uv sync --all-extras

# Run server with hot-reload
uv run uvicorn submerge.api:app --reload --port 8282

# Run all tests
uv run pytest

# Lint
uv run ruff check src tests
```

### Project Structure

```
src/submerge/
├── api.py              # App factory, middleware, lifespan, shared helpers, UI routes
├── routers/            # Modular API route handlers
│   ├── __init__.py
│   ├── history.py      # /api/history, /api/history/clear
│   ├── merge.py        # /api/merge, /api/batch-merge, /api/sync
│   ├── presets.py      # /api/presets (CRUD)
│   ├── queue.py        # /api/queue, /api/polls
│   ├── scanner.py      # /api/media, /scan, /api/frame-extract
│   ├── schedule.py     # /api/settings/schedule
│   └── settings.py     # /api/settings, /api/settings/default-template
├── config.py           # SubtoolsSettings model via Pydantic
├── hook.py             # Bazarr/Lingarr webhook processing, polling
├── merge.py            # Core bilingual subtitle merge logic
├── queue.py            # Persistent retry queue (SQLite)
├── scanner.py          # Media directory scanner
├── sync.py             # ffsubsync integration
├── cli.py              # CLI entry point
├── templates/          # Jinja2 HTML templates
└── static/             # CSS, JS static assets
```

## License

MIT
