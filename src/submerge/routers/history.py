"""History API routes — completed merge entries."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..api import _get_effective_settings, validate_path
from ..queue import clear_history, get_history, get_history_entries_by_ids

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/history")
def api_history(limit: int = 200):
    """Return completed merge history entries, newest first."""
    settings = _get_effective_settings()
    try:
        entries = get_history(limit=limit, settings=settings)
        return JSONResponse({"entries": entries, "count": len(entries)})
    except Exception as e:
        logger.error(f"History fetch error: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.post("/api/history/clear")
def api_history_clear():
    """Delete all completed (done/failed) entries from the queue table."""
    settings = _get_effective_settings()
    try:
        count = clear_history(settings=settings)
        logger.info(f"History cleared: {count} entries removed")
        return {"status": "ok", "removed": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e


@router.get("/api/history/export")
def api_history_export(ids: str = ""):
    """Export output files for given history entry IDs as a ZIP archive.

    Query params:
        ids: Comma-separated list of history entry IDs (e.g. ?ids=1,2,3).

    Returns:
        StreamingResponse with Content-Type: application/zip.
        400 if more than 50 IDs or empty ids list.
        404 if no exportable files found.
    """
    if not ids or not ids.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": "ids parameter required (comma-separated integers)",
            },
        )

    try:
        id_list = [int(i.strip()) for i in ids.split(",") if i.strip()]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "ids must be comma-separated integers"},
        ) from None

    if len(id_list) > 50:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Maximum 50 IDs allowed per export"},
        )

    settings = _get_effective_settings()
    entries = get_history_entries_by_ids(id_list, settings=settings)

    if not entries:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "No exportable files found for given IDs"},
        )

    # Collect files, validate paths, skip invalid
    zip_files: list[tuple[str, Path]] = []
    seen_names: dict[str, int] = {}
    for _idx, entry in enumerate(entries):
        for file_path_str in entry.get("output_files", []):
            try:
                file_path = validate_path(file_path_str, "output_file", check_media_root=True)
            except HTTPException:
                logger.warning(
                    "Export: skipping file outside media root for entry %d: %s",
                    entry["id"],
                    file_path_str,
                )
                continue
            if not file_path.exists():
                logger.warning(
                    f"Export: skipping missing file for entry {entry['id']}: {file_path_str}"
                )
                continue
            base_name = Path(file_path_str).name
            # Handle name collisions with index prefix
            if base_name in seen_names:
                seen_names[base_name] += 1
                arcname = f"{seen_names[base_name]}_{base_name}"
            else:
                seen_names[base_name] = 1
                arcname = base_name
            zip_files.append((arcname, file_path))

    if not zip_files:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "No exportable files found for given IDs"},
        )

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, file_path in zip_files:
            zf.write(str(file_path), arcname)
    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="submerge_export.zip"'},
    )
