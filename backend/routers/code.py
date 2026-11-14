"""Code router — GitNexus code analysis, cache endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.auth_middleware import require_admin

router = APIRouter()


def get_orchestrator(request: Request):
    return request.app.state.orchestrator


class CodeSearchRequest(BaseModel):
    query: str
    repo: Optional[str] = None


class CodeImpactRequest(BaseModel):
    symbol: str
    repo: Optional[str] = None
    depth: int = 3


class CodeContextRequest(BaseModel):
    symbol: str
    repo: Optional[str] = None


class CodeIndexRequest(BaseModel):
    repo_path: str


@router.get("/code/health")
async def code_health(request: Request, session: dict = Depends(require_admin)):
    """Health check del bridge GitNexus"""
    orchestrator = get_orchestrator(request)
    try:
        health = await orchestrator.get_gitnexus_health()
        return {"success": True, **health}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/code/repos")
async def code_list_repos(request: Request, session: dict = Depends(require_admin)):
    """Listar repositorios indexados en GitNexus"""
    orchestrator = get_orchestrator(request)
    try:
        ready = await orchestrator._ensure_gitnexus()
        if not ready:
            return {"success": False, "error": "GitNexus no disponible"}
        repos = await orchestrator.gitnexus.list_repos()
        return {"success": True, "repos": repos}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/code/search")
async def code_search(
    request: Request,
    body: CodeSearchRequest,
    session: dict = Depends(require_admin),
):
    """Búsqueda híbrida en el knowledge graph"""
    orchestrator = get_orchestrator(request)
    try:
        ready = await orchestrator._ensure_gitnexus()
        if not ready:
            return {"success": False, "error": "GitNexus no disponible"}

        import time as _time
        start = _time.perf_counter()
        result = await orchestrator.gitnexus.search(body.query, repo=body.repo)
        elapsed = _time.perf_counter() - start

        return {"success": True, "tool": "search", "result": result, "elapsed_time": round(elapsed, 3)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/code/impact")
async def code_impact(
    request: Request,
    body: CodeImpactRequest,
    session: dict = Depends(require_admin),
):
    """Análisis de blast radius de un símbolo"""
    orchestrator = get_orchestrator(request)
    try:
        ready = await orchestrator._ensure_gitnexus()
        if not ready:
            return {"success": False, "error": "GitNexus no disponible"}

        import time as _time
        start = _time.perf_counter()
        result = await orchestrator.gitnexus.impact(body.symbol, repo=body.repo, depth=body.depth)
        elapsed = _time.perf_counter() - start

        return {"success": True, "tool": "impact", "result": result, "elapsed_time": round(elapsed, 3)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/code/context")
async def code_context(
    request: Request,
    body: CodeContextRequest,
    session: dict = Depends(require_admin),
):
    """Vista 360° de un símbolo"""
    orchestrator = get_orchestrator(request)
    try:
        ready = await orchestrator._ensure_gitnexus()
        if not ready:
            return {"success": False, "error": "GitNexus no disponible"}

        import time as _time
        start = _time.perf_counter()
        result = await orchestrator.gitnexus.context(body.symbol, repo=body.repo)
        elapsed = _time.perf_counter() - start

        return {"success": True, "tool": "context", "result": result, "elapsed_time": round(elapsed, 3)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/code/changes")
async def code_detect_changes(
    request: Request,
    session: dict = Depends(require_admin),
    repo: Optional[str] = None,
):
    """Detectar cambios git mapeados a procesos"""
    orchestrator = get_orchestrator(request)
    try:
        ready = await orchestrator._ensure_gitnexus()
        if not ready:
            return {"success": False, "error": "GitNexus no disponible"}

        import time as _time
        start = _time.perf_counter()
        result = await orchestrator.gitnexus.detect_changes(repo=repo)
        elapsed = _time.perf_counter() - start

        return {"success": True, "tool": "detect_changes", "result": result, "elapsed_time": round(elapsed, 3)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/code/index")
async def code_index_repo(
    request: Request,
    body: CodeIndexRequest,
    session: dict = Depends(require_admin),
):
    """Indexar un repositorio (solo admin, puede tardar minutos)"""
    orchestrator = get_orchestrator(request)
    try:
        result = await orchestrator.trigger_gitnexus_index(body.repo_path)
        return {"success": result.get("success", False), **result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============ CACHE ============

@router.get("/cache/stats")
async def get_cache_stats(request: Request, session: dict = Depends(require_admin)):
    """Estadísticas del LLM Cache"""
    orchestrator = get_orchestrator(request)
    return orchestrator.llm_cache.stats


@router.delete("/cache/clear")
async def clear_cache(request: Request, session: dict = Depends(require_admin)):
    """Limpia el LLM Cache"""
    orchestrator = get_orchestrator(request)
    count = orchestrator.llm_cache.clear()
    return {"cleared": count, "message": f"Cache limpiado ({count} entradas)"}
