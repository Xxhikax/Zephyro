"""
Router REST para el sistema de agentes (Phase 4b).

Endpoints:
  GET  /api/agents                 — lista agentes disponibles
  GET  /api/agents/{name}          — detalle de un agente
  POST /api/agents/{name}/run      — invoca un agente con un task
  POST /api/agents/run             — invoca OrchestratorAgent (auto-clasifica)
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.auth_middleware import require_auth
from core.request_context import RequestContext

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    task: str
    force_agent: Optional[str] = None
    session_id: Optional[str] = None


def _get_orchestrator_agent(request: Request):
    return getattr(request.app.state, "orchestrator_agent", None)


@router.get("")
async def list_agents(request: Request, _session: dict = Depends(require_auth)) -> dict[str, Any]:
    """Lista agentes registrados con sus tools (skill names)."""
    oa = _get_orchestrator_agent(request)
    if oa is None:
        raise HTTPException(status_code=503, detail="OrchestratorAgent no inicializado")

    agents = []
    for agent in oa.get_agents().values():
        agents.append({
            "name": agent.name,
            "description": agent.description,
            "tools": list(agent.tools),
        })
    # Incluye el orchestrator agent en la lista
    agents.insert(0, {
        "name": oa.name,
        "description": oa.description,
        "tools": ["dev", "browser", "system", "memory"],
    })
    return {"agents": agents, "total": len(agents)}


@router.get("/{name}")
async def get_agent(name: str, request: Request, _session: dict = Depends(require_auth)) -> dict[str, Any]:
    oa = _get_orchestrator_agent(request)
    if oa is None:
        raise HTTPException(status_code=503, detail="OrchestratorAgent no inicializado")

    if name == oa.name:
        return {
            "name": oa.name,
            "description": oa.description,
            "tools": ["dev", "browser", "system", "memory"],
        }

    agent = oa.get_agent(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agente '{name}' no existe")
    return {
        "name": agent.name,
        "description": agent.description,
        "tools": list(agent.tools),
    }


@router.post("/run")
async def run_orchestrator(
    body: AgentRunRequest,
    request: Request,
    session: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Invoca OrchestratorAgent — auto-clasifica o usa force_agent."""
    oa = _get_orchestrator_agent(request)
    if oa is None:
        raise HTTPException(status_code=503, detail="OrchestratorAgent no inicializado")

    ctx = _build_context(session, body)
    result = await oa.handle(body.task, ctx, force_agent=body.force_agent)
    return _serialize_result(result)


@router.post("/{name}/run")
async def run_named_agent(
    name: str,
    body: AgentRunRequest,
    request: Request,
    session: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Invoca un agente específico por nombre."""
    oa = _get_orchestrator_agent(request)
    if oa is None:
        raise HTTPException(status_code=503, detail="OrchestratorAgent no inicializado")

    if name == oa.name:
        ctx = _build_context(session, body)
        result = await oa.handle(body.task, ctx, force_agent=body.force_agent)
        return _serialize_result(result)

    agent = oa.get_agent(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agente '{name}' no existe")

    ctx = _build_context(session, body)
    result = await agent.handle(body.task, ctx)
    return _serialize_result(result)


def _build_context(session: dict, body: AgentRunRequest) -> RequestContext:
    return RequestContext(
        user_role=session.get("role", "guest"),
        user_name=session.get("user_name"),
        authenticated=True,
        session_id=body.session_id or session.get("session_id", "rest-agent"),
        channel_type="rest",
    )


def _serialize_result(result) -> dict[str, Any]:
    return {
        "success": result.success,
        "response": result.response,
        "skill_used": result.skill_used,
        "evidence": result.evidence,
        "elapsed_ms": round(result.elapsed_ms, 2),
        "verification": result.verification,
        "metadata": result.metadata,
    }
