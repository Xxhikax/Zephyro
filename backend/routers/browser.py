"""
Router para inspección y control del BrowserController.

Endpoints:
- GET /api/browser/status        → health + queue + url actual
- POST /api/browser/kill          → drena cola y cierra browser (admin)
- POST /api/browser/restart       → kill + relanzar (admin)
- GET /api/browser/logs?tail=N    → snapshot de las últimas N líneas

Para stream live de logs, ver `browser:subscribe` / `browser:unsubscribe`
via WebSocket en routers/chat.py.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from core.auth_middleware import require_admin
from core.connection_registry import registry

router = APIRouter(tags=["browser"])


def _get_app_controller(request: Request):
    orch = getattr(request.app.state, "orchestrator", None)
    if not orch:
        return None
    return getattr(orch, "automation", None)


def _get_browser_or_none(request: Request):
    """Devuelve BrowserController solo si ya estaba instanciado (no lazy-init)."""
    ctrl = _get_app_controller(request)
    if not ctrl:
        return None
    # AppController.browser es @property lazy; chequear _browser interno
    return getattr(ctrl, "_browser", None)


@router.get("/browser/status")
async def browser_status(request: Request) -> dict[str, Any]:
    """Devuelve el estado actual del BrowserController sin forzar su inicialización."""
    browser = _get_browser_or_none(request)
    if browser is None:
        return {
            "initialized": False,
            "healthy": False,
            "state": "closed",
            "message": "browser no inicializado todavía",
        }

    healthy = False
    try:
        healthy = browser.is_healthy()
    except Exception:
        healthy = False

    current_url = None
    try:
        page = getattr(browser, "page", None)
        if page is not None and not page.is_closed():
            current_url = page.url
    except Exception:
        current_url = None

    return {
        "initialized": True,
        "healthy": healthy,
        "state": browser.get_state().value,
        "health": browser.get_health().value,
        "current_url": current_url,
        "session": browser.get_session_info(),
        "queue": browser.get_queue_status(),
    }


@router.post("/browser/kill")
async def browser_kill(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Drena la cola y cierra el browser. Requiere admin."""
    browser = _get_browser_or_none(request)
    if browser is None:
        return {"success": True, "message": "browser no estaba inicializado", "drained": 0}

    result = await browser.kill()

    # Broadcast status change
    await registry.broadcast({
        "type": "browser:status",
        "healthy": False,
        "state": "closed",
        "current_url": None,
    })

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message", "kill failed"))
    return result


@router.post("/browser/restart")
async def browser_restart(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """kill + relanzar. Requiere admin."""
    ctrl = _get_app_controller(request)
    if not ctrl:
        raise HTTPException(status_code=500, detail="AppController no disponible")

    # Forzar instanciación si no existe (a través de .browser property)
    try:
        browser = ctrl.browser
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"no se pudo obtener browser: {exc}")

    result = await browser.restart()

    # Broadcast status change
    healthy = False
    try:
        healthy = browser.is_healthy()
    except Exception:
        pass
    await registry.broadcast({
        "type": "browser:status",
        "healthy": healthy,
        "state": browser.get_state().value,
        "current_url": getattr(getattr(browser, "page", None), "url", None) if healthy else None,
    })

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message", "restart failed"))
    return result


@router.get("/browser/logs")
async def browser_logs(
    request: Request,
    tail: int = 50,
) -> dict[str, Any]:
    """Devuelve las últimas N líneas de playwright.log (snapshot)."""
    if tail < 1:
        tail = 50
    if tail > 1000:
        tail = 1000

    browser = _get_browser_or_none(request)
    if browser is None:
        # Aún sin browser podemos leer el archivo si existe
        from pathlib import Path
        log_path = Path("playwright.log")
        if not log_path.exists():
            return {"lines": [], "total": 0, "log_file": str(log_path)}
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            lines = [ln.rstrip("\n") for ln in lines[-tail:]]
            return {"lines": lines, "total": len(lines), "log_file": str(log_path)}
        except Exception as exc:
            return JSONResponse(
                {"error": str(exc), "lines": [], "total": 0},
                status_code=500,
            )

    lines = browser.tail_logs(tail)
    return {
        "lines": lines,
        "total": len(lines),
        "log_file": browser.config.log_file,
    }
