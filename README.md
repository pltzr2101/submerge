# SubMerge

Automated bilingual subtitle generation for your media stack. Combines two single-language subtitles into a professionally styled bilingual ASS file — with Web UI, Bazarr/Lingarr integration, and live style preview.

## Why?

Watching movies with someone who speaks another language? Platforms never offer bilingual subtitles, and manually merging SRT files fails because of desynchronization.

SubMerge downloads/extracts subtitles, synchronizes them against a reference, and merges everything into a single ASS file with distinct positioning — one language at bottom, one at top (or stacked).

## Quick Start

```bash
# Install
git clone https://github.com/pltzr2101/submerge.git && cd submerge
pip install -e .

# Merge two subtitles
submerge merge french.srt korean.srt -o bilingual.ass
```

## Features

- **CLI** — merge, sync, extract, probe subtitle tracks
- **FastAPI Server** — REST API on port 8282 with Web UI
- **Web UI** — Dashboard, Style Editor, Settings, Log Viewer
- **Bazarr Integration** — `/hook` webhook fires when Bazarr downloads subtitles
- **Lingarr Integration** — `/lingarr-hook` processes AI-translated subtitles
- **Background Retry** — SQLite-backed queue polls until all languages arrive
- **ISO 639 Aliasing** — Accepts `de`, `deu`, `ger`, `de-DE`, `de_DE` transparently
- **Style Presets** — 3 built-in presets + custom save/load via UI
- **Canvas Preview** — Real-time subtitle style preview in browser
- **Frame Extraction** — Extract video frames as style preview background
- **Basic Auth** — Optional password protection for the Web UI
- **Rate Limiting** — Per-IP rate limiting on all endpoints
- **Docker** — Ready-to-deploy container with healthcheck

## Prerequisites

- Python 3.10+
- ffmpeg (`brew install ffmpeg` / `apt install ffmpeg`)

## CLI Commands

### `merge` — Combine two SRTs into one bilingual ASS

```bash
submerge merge bottom.srt top.srt -o bilingual.ass \
  --color1 "#FFFFFF" --color2 "#FFFF00" \
  --fontsize 20 --layout stacked
```

Options: `--color1`, `--color2`, `--fontsize`, `--fontname`, `--outline`, `--shadow`, `--layout` (`top-bottom`|`stacked`), `--margin-bottom`, `--margin-top`, `--stacked-gap`.

### `sync` — Align subtitle timing

```bash
# Fast: sync against a reference subtitle
submerge sync off.srt --ref reference.srt -o synced.srt

# Slower: sync against video audio (uses ffmpeg+faster-whisper)
submerge sync off.srt --video movie.mkv -o synced.srt
```

### `extract` — Extract embedded subtitles from MKV

```bash
submerge extract movie.mkv --lang en -o english.srt
submerge extract movie.mkv --track 2 -o english.srt
```

### `list-tracks` — Show subtitle/audio tracks

```bash
submerge list-tracks movie.mkv
```

## Web UI

Point your browser to `http://localhost:8282/`:

| Page | Description |
|------|-------------|
| `/` | **Dashboard** — media overview, filter by status, one-click merge/sync, batch operations |
| `/styles` | **Style Editor** — two-tab (Bottom/Top) controls, Canvas preview, CJK font picker, presets, ASS export |
| `/settings` | **Settings** — view & override all SUBTOOLS_* env vars at runtime |
| `/logs/stream` | **Log Viewer** — SSE stream of merge operations |

## Docker Deployment

### Basic (standalone)

```bash
docker run -d -p 8282:8282 \
  -v /path/to/media:/data \
  -e SUBTOOLS_PAIRS="de-ko" \
  -e SUBTOOLS_UI_PASSWORD="secret" \
  ghcr.io/pltzr2101/submerge:latest
```

### ARR Stack Integration (Sonarr + Radarr + Bazarr + Lingarr)

Add to your `docker-compose.yml`:

```yaml
submerge:
  image: ghcr.io/pltzr2101/submerge:latest
  container_name: submerge
  environment:
    SUBTOOLS_PAIRS: "de-ko,en-ko"
    SUBTOOLS_MEDIA_ROOT: "/data"
    SUBTOOLS_UI_PASSWORD: "hunter2"
    SUBTOOLS_POLL_INTERVAL: "60"
    SUBTOOLS_RETRY_TIMEOUT_H: "48"
    SUBTOOLS_FONT_TOP: "Noto Sans KR"
  volumes:
    - /path/to/media:/data   # Same mount as Bazarr/Lingarr
  restart: unless-stopped
```

## Bazarr Integration

In Bazarr: **Settings → Subtitles → Post-Processing**, enable and set command:

```
/config/bazarr-hook.sh "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}"
```

With `/config/bazarr-hook.sh`:

```bash
#!/bin/sh
curl -sf -X POST "http://submerge:8282/hook" \
  --data-urlencode "video=$1" \
  --data-urlencode "subtitle=$2" \
  --data-urlencode "lang=$3"
```

If not all languages are available yet, SubMerge starts background polling (default: every 60s for up to 48h) and retries when Lingarr finishes translating.

## Lingarr Integration

Lingarr should be configured to call SubMerge's `/lingarr-hook` endpoint after each translation completes:

```
http://submerge:8282/lingarr-hook
```

Same POST format as the Bazarr hook. This triggers a fresh check — if the last missing language just arrived, the merge runs immediately.

## Environment Variables

### Required

| Variable | Example | Description |
|----------|---------|-------------|
| `SUBTOOLS_PAIRS` | `"de-ko,en-ko"` | Language pairs (bottom-top), comma-separated |

### Styling

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBTOOLS_COLOR_BOTTOM` | `#FFFFFF` | Bottom subtitle color |
| `SUBTOOLS_COLOR_TOP` | `#FFFF00` | Top subtitle color |
| `SUBTOOLS_FONTSIZE` | `18` | Font size |
| `SUBTOOLS_LAYOUT` | `top-bottom` | `top-bottom` or `stacked` |
| `SUBTOOLS_BOTTOM_FONTSIZE` | `20` | Bottom track font size |
| `SUBTOOLS_BOTTOM_BOLD` | `false` | Bold bottom text |
| `SUBTOOLS_BOTTOM_OUTLINE` | `2.0` | Bottom outline width |
| `SUBTOOLS_BOTTOM_SHADOW` | `1.0` | Bottom shadow depth |
| `SUBTOOLS_BOTTOM_MARGIN_V` | `30` | Bottom vertical margin |
| `SUBTOOLS_BOTTOM_MARGIN_H` | `20` | Bottom horizontal margin |
| `SUBTOOLS_BOTTOM_SPACING` | `0.0` | Bottom character spacing |
| `SUBTOOLS_FONT_BOTTOM` | `""` | Font for bottom (empty = Arial) |
| `SUBTOOLS_TOP_FONTSIZE` | `18` | Top track font size |
| `SUBTOOLS_TOP_BOLD` | `false` | Bold top text |
| `SUBTOOLS_TOP_OUTLINE` | `2.0` | Top outline width |
| `SUBTOOLS_TOP_SHADOW` | `1.0` | Top shadow depth |
| `SUBTOOLS_TOP_MARGIN_V` | `15` | Top vertical margin |
| `SUBTOOLS_TOP_MARGIN_H` | `20` | Top horizontal margin |
| `SUBTOOLS_TOP_SPACING` | `0.0` | Top character spacing |
| `SUBTOOLS_FONT_TOP` | `Noto Sans KR` | Font for top (CJK) |
| `SUBTOOLS_STACKED_GAP` | `8` | Gap between stacked lines |

### Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBTOOLS_MEDIA_ROOT` | `/data` | Media files root directory |
| `SUBTOOLS_POLL_INTERVAL` | `60` | Background retry interval (seconds) |
| `SUBTOOLS_RETRY_TIMEOUT_H` | `48` | Abandon pending merges after N hours |
| `SUBTOOLS_UI_PASSWORD` | `""` | Basic auth password for Web UI (empty = no auth) |
| `SUBTOOLS_RATE_LIMIT_RPM` | `30` | Max requests per minute per IP (0 = disabled) |

## API Reference

### Webhooks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/hook` | POST | Bazarr post-processing webhook |
| `/lingarr-hook` | POST | Lingarr post-processing webhook |
| `/health` | GET | Health check |

### Media & Merge

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/media` | GET | List all media with subtitle status |
| `/api/merge` | POST | Manually trigger merge for one video |
| `/api/sync` | POST | Synchronize a subtitle file |
| `/scan` | POST | Scan all directories, start missing merges |
| `/api/polls` | GET | List active background polling jobs |

### Queue

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/queue` | GET | List all queue entries |
| `/api/queue/{id}/remove` | POST | Remove a queue entry |
| `/api/queue/{id}/retry` | POST | Retry a queue entry now |

### Style Presets

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/presets` | GET | List all presets |
| `/api/presets/{name}` | GET | Get preset styles |
| `/api/presets` | POST | Save custom preset |
| `/api/presets/{name}` | DELETE | Delete custom preset |
| `/api/frame-extract` | GET | Extract a video frame (jpg) |

### UI & Monitoring

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI Dashboard |
| `/styles` | GET | Style Editor |
| `/settings` | GET | Settings page |
| `/logs/stream` | GET | SSE log stream |
| `/api/settings` | POST | Update runtime settings |

### Webhook POST Format

```
POST /hook
Content-Type: application/x-www-form-urlencoded

video=/data/media/movie.mkv&subtitle=/data/media/movie.de.srt&lang=de
```

Response (200): `{"status": "merged", "files": ["/data/media/movie.de-ko.ass"]}` or `{"status": "polling", "present": ["de"], "missing": ["ko"]}` or `{"status": "skipped", "reason": "..."}`.

## Development

```bash
# Setup
uv sync --all-extras

# Run tests
uv run pytest

# Lint
uv run ruff check src tests

# Run server (dev)
uv run uvicorn submerge.api:app --reload
```

## Troubleshooting

**Bazarr hook not triggering:**
1. Verify hook script is at `/config/bazarr-hook.sh` inside Bazarr container
2. Check `curl http://submerge:8282/health` from inside Bazarr container
3. View logs: `docker logs submerge`

**Merge not happening (languages missing):**
1. Check queue: `curl http://submerge:8282/api/queue`
2. Polling delays: Lingarr may take minutes to finish translation
3. Timeout: entries older than `SUBTOOLS_RETRY_TIMEOUT_H` hours are abandoned

**CJK (Korean/Chinese/Japanese) text not displaying:**
1. Set `SUBTOOLS_FONT_TOP="Noto Sans KR"` (or appropriate CJK font)
2. Ensure the font is installed in the container
3. Check `submerge list-tracks movie.mkv` to verify embedded subtitle format

**"No text subtitle tracks found"** — The video has image-based subtitles (PGS/VOBSUB), which aren't supported.

## License

MIT
