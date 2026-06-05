"""Runtime settings and default-template API routes."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ..api import (
    _load_app_settings,
    _load_presets,
    _runtime_settings,
    _runtime_settings_lock,
    _runtime_settings_to_response,
    _save_app_settings,
)
from ..config import SubtoolsSettings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/settings")
async def api_settings(request: Request):
    """Apply runtime settings (in-memory only, not persisted)."""
    try:
        body = await request.json()
        known_fields = set(SubtoolsSettings.model_fields.keys())

        with _runtime_settings_lock:
            # -- Special-case: pairs (parse validation via _parse_pairs_string) --
            if "pairs" in body and body["pairs"]:
                pairs_str = str(body["pairs"]).strip()
                if pairs_str:
                    from ..config import _parse_pairs_string

                    try:
                        _parse_pairs_string(pairs_str)
                        _runtime_settings["pairs"] = pairs_str
                    except ValueError as e:
                        raise HTTPException(
                            status_code=422,
                            detail={"status": "error", "message": f"Invalid pairs: {e}"},
                        ) from e

            # -- Special-case: media_root (I/O path check) --
            if "media_root" in body:
                resolved = Path(str(body["media_root"])).resolve()
                if not resolved.is_dir():
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "status": "error",
                            "message": f"media_root is not a directory: {resolved}",
                        },
                    )
                body["media_root"] = str(resolved)

            # -- Build candidate from known model fields (exclude pairs — already handled) --
            candidate = {k: v for k, v in body.items() if k in known_fields and k != "pairs_raw"}

            if candidate:
                try:
                    validated = SubtoolsSettings.with_overrides(**candidate)
                except ValidationError as e:
                    raise HTTPException(
                        status_code=422,
                        detail={"status": "error", "message": str(e)},
                    ) from e
                # Merge validated values into runtime settings
                for field_name in candidate:
                    val = getattr(validated, field_name, None)
                    # Allow empty font strings to pass through
                    if val is not None or field_name in ("font_bottom", "font_top"):
                        _runtime_settings[field_name] = val

        logger.info(f"Runtime settings updated: {list(_runtime_settings.keys())}")
        return {"status": "ok", "settings": _runtime_settings_to_response()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Settings update error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.get("/api/settings/default-template")
def api_get_default_template():
    """Get the current default style template name."""
    app_settings = _load_app_settings()
    return {"default_template": app_settings.get("default_template", "")}


@router.post("/api/settings/default-template")
async def api_set_default_template(request: Request):
    """Set the default style template name."""
    try:
        body = await request.json()
        name = body.get("template", "").strip()
        if name:
            presets = _load_presets()
            if name not in presets:
                raise HTTPException(
                    status_code=400,
                    detail={"status": "error", "message": f"Unknown template: {name}"},
                )
        app_settings = _load_app_settings()
        if name:
            app_settings["default_template"] = name
        elif "default_template" in app_settings:
            del app_settings["default_template"]
        _save_app_settings(app_settings)
        return {"status": "ok", "default_template": name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
