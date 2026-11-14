"""Canvas router — endpoint REST para datos de Canvas LMS.

GET /canvas/summary — cursos, notas, entregas faltantes y anuncios.
Auth via canonical Depends(require_auth).
Caché de 10 minutos en memoria (sistema de un usuario).
"""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from core.auth_middleware import require_auth

router = APIRouter()

# Caché simple: (data, timestamp)
_cache: dict[str, tuple[object, float]] = {}
_CACHE_TTL = 600.0  # 10 minutos


def _cached_or_fresh(key: str):
    entry = _cache.get(key)
    if entry and time.monotonic() - entry[1] < _CACHE_TTL:
        return entry[0]
    return None


def _store(key: str, value: object):
    _cache[key] = (value, time.monotonic())


@router.get("/canvas/summary")
async def canvas_summary(request: Request, session: dict = Depends(require_auth)):
    cached = _cached_or_fresh("summary")
    if cached is not None:
        return cached

    try:
        from modules.canvas.client import CanvasClient
    except ImportError:
        return {"configured": False, "error": "módulo canvas no disponible"}

    client = CanvasClient()
    if not client.is_configured():
        return {"configured": False}

    try:
        grades_raw, missing_raw, announcements_raw = await asyncio.gather(
            client.get_course_grades(),
            client.get_missing_submissions(),
            client.get_announcements(limit=10),
        )
    except Exception as exc:
        return {"configured": True, "error": str(exc)}

    def fmt_missing(item: dict) -> dict:
        due = item.get("due_at")
        return {
            "title": item.get("title", ""),
            "course": item.get("course", ""),
            "due_str": due.strftime("%d/%m/%Y %H:%M") if due else None,
            "points_possible": item.get("points_possible"),
        }

    result = {
        "configured": True,
        "grades": grades_raw,           # [{course, score, grade}]
        "missing": [fmt_missing(m) for m in missing_raw],
        "announcements": announcements_raw,  # [{title, course, posted_at, message}]
    }
    _store("summary", result)
    return result


@router.post("/canvas/cache/clear")
async def canvas_cache_clear(request: Request, session: dict = Depends(require_auth)):
    _cache.clear()
    return {"ok": True}
