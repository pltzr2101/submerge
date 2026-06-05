"""Subtitle preview API route — returns first N cues of an .ass/.srt file."""

from __future__ import annotations

import logging

import pysubs2
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..api import validate_path

logger = logging.getLogger(__name__)
router = APIRouter()


def _ms_to_str(ms: int) -> str:
    """Convert milliseconds to ASS/SRT time string (H:MM:SS.cc)."""
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    cs = (ms % 1000) // 10
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"


@router.get("/api/subtitle-preview")
def api_subtitle_preview(path: str, limit: int = 20):
    """Return first `limit` cues of a subtitle file as JSON."""
    sub_path = validate_path(path, "path", check_media_root=True)
    if not sub_path.exists():
        raise HTTPException(
            status_code=404, detail={"status": "error", "message": "File not found"}
        )
    try:
        subs = pysubs2.load(str(sub_path))
        cues = [
            {
                "index": i,
                "start": _ms_to_str(e.start),
                "end": _ms_to_str(e.end),
                "text": e.plaintext.strip(),
                "style": e.style,
            }
            for i, e in enumerate(subs[:limit])
        ]
        return JSONResponse({"path": str(sub_path), "total_events": len(subs), "preview": cues})
    except Exception as e:
        logger.error(f"Subtitle preview error for {sub_path.name}: {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": str(e)}) from e
