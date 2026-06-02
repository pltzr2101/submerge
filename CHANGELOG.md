## [2026-06-02]

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
