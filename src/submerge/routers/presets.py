"""Style preset API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..api import (
    _DEFAULT_PRESETS,
    _load_app_settings,
    _load_presets,
    _save_custom_presets,
)

router = APIRouter()


@router.get("/api/presets")
def api_presets_list():
    """List all available style presets (built-in + custom)."""
    presets = _load_presets()
    return {"presets": [{"name": k} for k in sorted(presets.keys())]}


@router.get("/api/presets/{name}")
def api_presets_get(name: str):
    """Get the style fields for a specific preset."""
    presets = _load_presets()
    if name not in presets:
        raise HTTPException(
            status_code=404, detail={"status": "error", "message": "Preset not found"}
        )
    return {"name": name, "styles": presets[name]}


@router.post("/api/presets")
async def api_presets_save(request: Request):
    """Save a new custom style preset."""
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        styles = body.get("styles", {})
        if not name:
            raise HTTPException(
                status_code=400, detail={"status": "error", "message": "Name required"}
            )
        if name in _DEFAULT_PRESETS:
            raise HTTPException(
                status_code=400,
                detail={"status": "error", "message": "Cannot override built-in preset"},
            )
        # Validate style keys against known fields from _DEFAULT_PRESETS
        known_keys = set(next(iter(_DEFAULT_PRESETS.values())))
        unknown = [k for k in styles if k not in known_keys]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={"status": "error", "message": f"Unknown style fields: {unknown}"},
            )
        presets = _load_presets()
        presets[name] = styles
        _save_custom_presets(presets)
        return {"status": "ok", "name": name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.delete("/api/presets/{name}")
def api_presets_delete(name: str):
    """Delete a custom style preset (built-in presets cannot be deleted)."""
    if name in _DEFAULT_PRESETS:
        raise HTTPException(
            status_code=400, detail={"status": "error", "message": "Cannot delete built-in preset"}
        )

    presets = _load_presets()
    if name not in presets:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Preset not found"},
        )

    # Prevent deleting the currently active default template
    app_settings = _load_app_settings()
    default_template = app_settings.get("default_template", "")
    if name == default_template:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Cannot delete the active default template"},
        )

    del presets[name]
    _save_custom_presets(presets)
    return {"status": "ok", "deleted": name}
