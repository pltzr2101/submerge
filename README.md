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

### 1. Deploy

```bash
cp docker-compose.example.yml docker-compose.yml
# Edit SUBTOOLS_PAIRS and volume path
docker compose up -d
```

**Environment variables:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUBTOOLS_PAIRS` | Yes | - | Language pairs: `"fr-pl,en-pl"` |
| `SUBTOOLS_COLOR_BOTTOM` | No | `#FFFFFF` | Bottom subtitle color |
| `SUBTOOLS_COLOR_TOP` | No | `#FFFF00` | Top subtitle color |
| `SUBTOOLS_FONTSIZE` | No | `18` | Font size (8-72) |
| `SUBTOOLS_LAYOUT` | No | `top-bottom` | `top-bottom` or `stacked` |

### 2. Configure Bazarr

```bash
# Copy hook script
cp scripts/bazarr-hook.sh /path/to/bazarr/config/
chmod +x /path/to/bazarr/config/bazarr-hook.sh
```

In Bazarr: **Settings > Subtitles > Post-Processing**, enable and set:

```
/config/bazarr-hook.sh "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}"
```

> Quotes around variables are required for paths with spaces.

### 3. Network

**Same stack (recommended):** If you add SubMerge to your existing Bazarr/Sonarr stack, they share a network automatically. Use `http://submerge:8282` in the hook script.

**Separate stacks:** Create a shared network with `docker network create media`, then add to both stacks:
```yaml
networks:
  media:
    external: true
```

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
