# AGENTS.md

Submerge is a bilingual subtitle merge service for ARR stacks: FastAPI backend (`src/submerge/api.py`),
Jinja2 web UI, and a Click CLI. Python >= 3.10 (CI and Docker use 3.11), managed with **uv** (`uv.lock`).
`src/` layout; package data includes `templates/` and `static/`.

## Commands

```bash
uv sync --all-extras                      # install runtime + dev deps (pytest, ruff, ...)

uv run pytest                              # all tests (428 passing as of 2026-11)
uv run pytest --cov=submerge --cov-report=term-missing --cov-fail-under=64   # what CI runs

uv run ruff check src tests                # lint (CI enforces this)
uv run ruff format src tests               # format (CI runs: ruff format --check src tests)

uv run uvicorn submerge.api:app --reload --port 8282   # dev server; UI on :8282, health at /health
uv run submerge --help                     # CLI: merge, sync, extract, list-tracks, serve
```

Lint config lives in `pyproject.toml` `[tool.ruff]`: line length 100, `target-version = "py310"`,
selects `E, F, I, B, UP, C4, SIM`. There is no Makefile and no pre-commit config.

## Layout

- `src/submerge/api.py` — `create_app()` factory; module-level `app = create_app()`
- `src/submerge/routers/` — route modules (merge, scanner, history, queue, repair, schedule, ...)
- `src/submerge/config.py` — pydantic-settings `SubtoolsSettings`, env prefix `SUBTOOLS_`, reads `.env`
  (see `.env.example`); `get_settings()` is `@lru_cache`
- `src/submerge/merge.py`, `sync.py`, `repair.py`, `scanner.py` — core subtitle logic
- `src/submerge/queue.py` — persistent retry queue (SQLite)
- `tests/unit/`, `tests/integration/`, plus a few `tests/test_*.py`; fixtures in `tests/fixtures/`
- `.github/workflows/ci.yml` — lint, format check, tests, coverage >= 64%

## Gotchas

- **Local dev server needs `SUBTOOLS_CONFIG_DIR`**: the default config dir is `/config`, which is a
  Docker path — without overriding it (e.g. `SUBTOOLS_CONFIG_DIR=/tmp/submerge uv run uvicorn ...`)
  the app fails at startup with `PermissionError`.
- **Settings are cached**: `get_settings()` is lru-cached and app state is captured at
  `create_app()` time. After changing env vars in a test, call `get_settings.cache_clear()`;
  to re-read app-level config, tests `importlib.reload(submerge.api)`.
- **UI tests parse source files with regex**: `tests/unit/test_css.py` and `test_dropdown_js.py`
  (and similar) read `static/style.css` and `templates/*.html` directly and assert on their text.
  Edits to CSS, templates, or inline JS can break tests without any Python import changing.
- **Config isolation is automatic in tests**: an autouse fixture in `tests/conftest.py` points
  `SUBTOOLS_CONFIG_DIR` at a tmp dir and clears the settings cache for every test. Use
  `get_settings_for_test(**overrides)` to build settings with overrides.
- **ffmpeg/ffsubsync/alass are optional at runtime**: tests pass without them installed; `/health`
  reports `degraded`. CI installs ffmpeg.
- **Version strings live in three places and are currently out of sync**: `pyproject.toml`,
  `src/submerge/__init__.py` (`__version__`), and release entries in `CHANGES.md`. Update all of
  them deliberately when bumping.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, ...). Notable
  changes are recorded in `CHANGES.md` (Keep a Changelog format).
