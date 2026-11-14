"""
Router para shell contextual.

Endpoints:
- GET /api/context/current        → snapshot del contexto detectado actual
- POST /api/context/workspace     → override manual del proyecto (10min lock)
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from core.auth_middleware import require_admin
from core.context_detector import detector

router = APIRouter(tags=["context"])


@router.get("/context/current")
async def context_current(request: Request) -> dict[str, Any]:
    """Devuelve el último contexto detectado."""
    return detector.get_current()


@router.post("/context/workspace")
async def context_set_workspace(
    request: Request,
    name: str,
    path: Optional[str] = None,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """
    Override manual del proyecto activo. Bloquea auto-detection por 10 min.
    Requiere admin.
    """
    name = (name or "").strip()
    if not name:
        return JSONResponse({"error": "name requerido"}, status_code=400)
    result = await detector.set_manual_workspace(name, path)
    return {"success": True, "context": result}
