"""Diagnostic router — start / resume / cancel DiagnosticAgent runs from Tauri."""
import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from core.auth_middleware import require_admin

router = APIRouter()
log = logging.getLogger("zephyro.diagnostic.router")


def _session_user_id(session: dict) -> str:
    return str(session.get("user_id") or session.get("username") or "unknown")


def _log_file_path() -> str:
    return os.getenv(
        "ZEPHYRO_LOG_FILE",
        str(Path(__file__).parent.parent.parent / "logs" / "backend.log"),
    )


def _code_root() -> str:
    return str(Path(__file__).parent.parent)


async def _run_diagnostic_task(app, user_id: str, request_id: str, n_lines: int) -> None:
    """Background task: runs DiagnosticAgent using the WS gate."""
    try:
        from subagents.diagnostic_agent import DiagnosticAgent

        gate = app.state.diagnostic_gate
        orchestrator = app.state.orchestrator

        agent = DiagnosticAgent(
            llm_manager=orchestrator.llm,
            gate=gate,
            log_file=_log_file_path(),
            code_root=_code_root(),
        )

        # For the WS gate, chat_id is actually the request_id — the gate uses it
        # to look up user_id in the store and push messages to the correct WS.
        result = await agent.run(chat_id=request_id, n_lines=n_lines)
        gate._store.update(request_id, status="completed", result_summary=result)
    except Exception as exc:
        log.exception("Diagnostic task %s failed: %s", request_id, exc)
        try:
            app.state.diagnostic_gate._store.update(
                request_id, status="error", error=str(exc)
            )
        except Exception:
            pass


@router.post("/self/diagnose")
async def start_diagnose(request: Request, n_lines: int = 500, session: dict = Depends(require_admin)):
    """Arranca un diagnóstico en background. Retorna request_id inmediatamente."""
    user_id = _session_user_id(session)

    gate = getattr(request.app.state, "diagnostic_gate", None)
    if gate is None:
        raise HTTPException(status_code=503, detail="DiagnosticGate no inicializado")

    request_id = await gate.start(user_id)
    asyncio.create_task(_run_diagnostic_task(request.app, user_id, request_id, n_lines))

    return {"success": True, "request_id": request_id}


@router.get("/self/diagnose/pending")
async def list_pending(request: Request, session: dict = Depends(require_admin)):
    """Lista diagnósticos pendientes del usuario (para resumir tras reconexión)."""
    user_id = _session_user_id(session)

    gate = getattr(request.app.state, "diagnostic_gate", None)
    if gate is None:
        return {"success": True, "pending": []}
    pending = gate._store.list_pending(user_id=user_id)
    return {"success": True, "pending": pending}


@router.post("/self/diagnose/{request_id}/cancel")
async def cancel_diagnose(request: Request, request_id: str, session: dict = Depends(require_admin)):
    """Cancela un diagnóstico en curso y borra su estado persistido."""
    gate = getattr(request.app.state, "diagnostic_gate", None)
    if gate is None:
        raise HTTPException(status_code=503, detail="DiagnosticGate no inicializado")

    gate.cancel(request_id)
    gate._store.delete(request_id)
    return {"success": True, "cancelled": request_id}
