# SubMerge

CLI for generating synchronized bilingual subtitles.

## Why?

Watching movies with someone who speaks another language? Platforms never offer bilingual subtitles, and manually merging SRT files fails because of desynchronization.

SubMerge extracts embedded subtitles from video files, synchronizes external subtitles against them, and merges everything into a single ASS file with distinct positioning (one language at bottom, one at top).

## Quick Start

```bash
# Install
git clone https://github.com/b4stOss/submerge.git && cd submerge
uv sync  # or: pip install -e .

# Merge two subtitles
submerge merge french.srt polish.srt -o bilingual.ass
```

## Prerequisites

- Python 3.10+
- ffmpeg (`brew install ffmpeg` / `apt install ffmpeg`)

## CLI Commands

### List subtitle tracks

```bash
submerge list-tracks movie.mkv
```

### Extract a subtitle track

```bash
submerge extract movie.mkv --lang en -o english.srt
submerge extract movie.mkv --track 2 -o english.srt
```

### Synchronize subtitles

```bash
# Fast: sync against a reference subtitle
submerge sync french.srt --ref english.srt -o french_synced.srt

# Slower: sync against video audio
submerge sync french.srt --video movie.mkv -o french_synced.srt
```

### Merge into bilingual

```bash
submerge merge french.srt polish.srt -o bilingual.ass

# With custom styling
submerge merge fr.srt pl.srt -o output.ass \
  --color1 "#FFFFFF" \
  --color2 "#FFFF00" \
  --fontsize 20 \
  --layout stacked
```

### Full workflow

```bash
# 1. Find available subtitles
submerge list-tracks movie.mkv

# 2. Extract reference (usually English)
submerge extract movie.mkv --lang en -o reference.srt

# 3. Sync your subtitles against reference
submerge sync french.srt --ref reference.srt -o fr_synced.srt
submerge sync polish.srt --ref reference.srt -o pl_synced.srt

# 4. Merge
submerge merge fr_synced.srt pl_synced.srt -o bilingual.ass
```

## Docker + Bazarr Integration

SubMerge can run as a service that automatically generates bilingual subtitles when Bazarr downloads new ones.

### 1. Add SubMerge to your existing stack

Add the SubMerge service to your existing `docker-compose.yml` (where Bazarr, Sonarr, etc. already are):

```yaml
services:
  bazarr:
    image: lscr.io/linuxserver/bazarr:latest
    # ... your existing bazarr config ...

  submerge:
    image: ghcr.io/b4stoss/submerge:latest
    container_name: submerge
    environment:
      SUBTOOLS_PAIRS: "fr-pl,en-pl"  # Your language pairs (bottom-top)
      # Optional styling:
      # SUBTOOLS_COLOR_BOTTOM: "#FFFFFF"
      # SUBTOOLS_COLOR_TOP: "#FFFF00"
      # SUBTOOLS_FONTSIZE: "18"
      # SUBTOOLS_LAYOUT: "top-bottom"
    volumes:
      - /path/to/data:/data  # Same as Bazarr
    restart: unless-stopped
```

> **Important:** SubMerge must see files at the same paths as Bazarr. If Bazarr has `-v /volume1/data:/data`, use the same for SubMerge.

**Environment variables:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUBTOOLS_PAIRS` | Yes | - | Language pairs: `"fr-pl,en-pl"` |
| `SUBTOOLS_COLOR_BOTTOM` | No | `#FFFFFF` | Bottom subtitle color |
| `SUBTOOLS_COLOR_TOP` | No | `#FFFF00` | Top subtitle color |
| `SUBTOOLS_FONTSIZE` | No | `18` | Font size (8-72) |
| `SUBTOOLS_LAYOUT` | No | `top-bottom` | `top-bottom` or `stacked` |

### 2. Setup the hook script

Create `/path/to/bazarr/config/bazarr-hook.sh`:

```bash
#!/bin/sh
SUBMERGE_URL="${SUBMERGE_URL:-http://submerge:8282/hook}"
curl -sf -X POST "$SUBMERGE_URL" \
  --data-urlencode "video=$1" \
  --data-urlencode "subtitle=$2" \
  --data-urlencode "lang=$3"
```

Then make it executable:
```bash
chmod +x /path/to/bazarr/config/bazarr-hook.sh
```

In Bazarr: **Settings > Subtitles > Post-Processing**, enable and set:

```
/config/bazarr-hook.sh "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}"
```

> Quotes around variables are required for paths with spaces.

### How it works

When Bazarr downloads a subtitle, it triggers SubMerge via the hook script. SubMerge then:

1. Checks if all languages from `SUBTOOLS_PAIRS` are available for that video
2. If not → does nothing, waits for the missing subtitles
3. If yes → generates the bilingual `.ass` file(s) automatically

The original subtitle files (`.srt`) are **not modified** — SubMerge only creates additional `.ass` files. You can always choose between the original single-language subtitles or the merged bilingual ones in your media player.

Plex/Jellyfin will detect the new subtitle files automatically.

**Note:** SubMerge processes all videos that Bazarr downloads subtitles for. There's currently no way to limit it to specific folders or videos.

## Troubleshooting

**"No text subtitle tracks found"** — The video only has image-based subtitles (PGS/VOBSUB), which aren't supported.

**Large sync offset (> 5s)** — Your SRT probably doesn't match the video version.

**Bazarr hook not working:**
1. Check SubMerge is running: `curl http://submerge:8282/health`
2. Check logs: `docker logs submerge`
3. Verify network connectivity between containers

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
```

## License

MIT
