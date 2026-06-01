# Submerge v2.0.0 - Web UI & Production Stack

See git log for full details. Key changes:

## New Features (v2.0.0)

- **Web UI Dashboard** - media overview, filter, one-click merge/sync, batch
- **Style Editor** - Canvas preview, CJK fonts, presets, ASS export
- **Settings page** - runtime override of all config
- **Log Viewer** - SSE stream
- **Lingarr webhook** - `/lingarr-hook`
- **SQLite retry queue** - background polling until all languages arrive
- **ISO 639 langmap** - normalize de/deu/ger/de-DE transparently
- **Style presets API** - 3 built-in + custom save/load
- **Frame extraction** - video frame as style preview background
- **Basic Auth + Rate Limiting** middleware
- **Expanded config** - per-language fontsize, color, outline, shadow, bold, margin, spacing, CJK fonts
- **Expanded MergeConfig** - full ASS style control

## v1.1.0 - Web UI & Reliable Bazarr Integration

- Web UI dashboard + settings + log viewer
- Polling fallback for missing languages
- Robust 2/3-letter ISO code matching
- Media scanner module
- Runtime settings override

## v1.0.0 - Initial Release

- CLI: merge, sync, extract, list-tracks
- Bazarr /hook webhook
- SRT to bilingual ASS (top-bottom / stacked)
- Subtitle sync (reference SRT or video audio)
