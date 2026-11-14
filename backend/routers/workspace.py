"""Workspace router — file workspace, git, session workspace, profile, summaries."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth_middleware import require_admin

router = APIRouter()


def get_orchestrator(request: Request):
    return request.app.state.orchestrator


# ============ FILE WORKSPACE ============

@router.get("/workspace/info")
async def workspace_info(request: Request, session: dict = Depends(require_admin)):
    """Información del workspace de archivos"""
    orchestrator = get_orchestrator(request)
    if not orchestrator.file_ops:
        return {"error": "File operations no disponible"}
    return {"info": orchestrator.file_ops.workspace_info()}


@router.post("/workspace/read")
async def read_file(request: Request, session: dict = Depends(require_admin)):
    """Lee un archivo"""
    data = await request.json()
    path = data.get("path", "")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    orchestrator = get_orchestrator(request)
    if not orchestrator.file_ops:
        return {"error": "File operations no disponible"}
    success, content = orchestrator.file_ops.read(path)
    return {"success": success, "content": content}


@router.post("/workspace/create")
async def create_file_endpoint(request: Request, session: dict = Depends(require_admin)):
    """Crea un archivo en el workspace"""
    data = await request.json()
    path = data.get("path", "")
    content = data.get("content", "")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    orchestrator = get_orchestrator(request)
    if not orchestrator.file_ops:
        return {"error": "File operations no disponible"}
    success, msg = orchestrator.file_ops.create_file(path, content)
    return {"success": success, "message": msg}


# ============ GIT ============

@router.post("/git/status")
async def git_status_endpoint(request: Request, session: dict = Depends(require_admin)):
    """Git status del repo en el path dado"""
    data = await request.json()
    path = data.get("path", None)
    orchestrator = get_orchestrator(request)
    if not orchestrator.git_ops:
        return {"error": "Git operations no disponible. Instala: pip install GitPython"}
    orchestrator.git_ops.repo_path = path or orchestrator.git_ops.repo_path
    success, result = await orchestrator.git_ops.status(path)
    return {"success": success, "result": result}


@router.get("/git/info")
async def git_info_endpoint(request: Request, path: str = None, session: dict = Depends(require_admin)):
    """Información del repositorio Git"""
    orchestrator = get_orchestrator(request)
    if not orchestrator.git_ops:
        return {"error": "Git operations no disponible"}
    info = await orchestrator.git_ops.get_repo_info(path)
    return info


# ============ USER PROFILE ============

@router.get("/profile")
async def get_user_profile(request: Request, token: Optional[str] = None):
    """Devuelve el perfil completo del usuario."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.user_profile:
            return {"profile": orchestrator.user_profile.get_full_profile()}
        return {"profile": None, "message": "UserProfile no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/profile/set")
async def set_profile_data(request: Request, token: Optional[str] = None):
    """Establece un dato del perfil (key/value)."""
    data = await request.json()
    key = data.get("key")
    value = data.get("value")
    if not key:
        raise HTTPException(status_code=400, detail="key requerido")
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.user_profile:
            orchestrator.user_profile.set(key, value)
            return {"ok": True}
        return {"ok": False, "message": "UserProfile no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/profile/preference")
async def set_preference(request: Request, token: Optional[str] = None):
    """Establece una preferencia del usuario."""
    data = await request.json()
    key = data.get("key")
    value = data.get("value")
    if not key:
        raise HTTPException(status_code=400, detail="key requerido")
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.user_profile:
            orchestrator.user_profile.set_preference(key, value)
            return {"ok": True}
        return {"ok": False, "message": "UserProfile no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profile/summary")
async def get_profile_summary(request: Request):
    """Devuelve el resumen del perfil (para contexto LLM)."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.user_profile:
            return {"summary": orchestrator.user_profile.get_summary()}
        return {"summary": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ CONVERSATION SUMMARIES ============

@router.get("/summaries")
async def get_summaries(request: Request, token: Optional[str] = None):
    """Lista todos los resúmenes de conversación."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.summarizer:
            return {
                "summaries": orchestrator.summarizer.get_all_summaries(),
                "stats": orchestrator.summarizer.get_stats()
            }
        return {"summaries": [], "message": "Summarizer no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summaries/context")
async def get_summaries_context(request: Request):
    """Devuelve el contexto de resúmenes para incluir en LLM."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.summarizer:
            return {"context": orchestrator.summarizer.get_context_for_llm()}
        return {"context": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/summaries/clear")
async def clear_summaries(request: Request, session: dict = Depends(require_admin)):
    """Borra todos los resúmenes."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.summarizer:
            orchestrator.summarizer.clear_summaries()
            return {"ok": True}
        return {"ok": False, "message": "Summarizer no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ SESSION WORKSPACES ============

@router.get("/workspaces")
async def list_workspaces(request: Request, token: Optional[str] = None):
    """Lista todos los workspaces."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.workspace:
            return {
                "workspaces": orchestrator.workspace.list_workspaces(),
                "active": orchestrator.workspace.get_active_workspace()
            }
        return {"workspaces": [], "message": "Workspace no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workspaces/create")
async def create_workspace(request: Request, token: Optional[str] = None):
    """Crea y activa un nuevo workspace."""
    data = await request.json()
    name = data.get("name", "").strip()
    description = data.get("description", "")
    if not name:
        raise HTTPException(status_code=400, detail="name requerido")
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.workspace:
            orchestrator.workspace.create_workspace(name, description)
            orchestrator.workspace.switch_workspace(name)
            return {"ok": True, "name": name}
        return {"ok": False, "message": "Workspace no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workspaces/switch")
async def switch_workspace(request: Request, token: Optional[str] = None):
    """Cambia el workspace activo."""
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name requerido")
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.workspace:
            orchestrator.workspace.switch_workspace(name)
            return {"ok": True, "active": name}
        return {"ok": False, "message": "Workspace no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspaces/active")
async def get_active_workspace(request: Request):
    """Devuelve el workspace activo con todo su contenido."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.workspace:
            ws = orchestrator.workspace.get_active_workspace()
            if ws:
                return orchestrator.workspace.get_full_workspace(ws)
            return {"active": None}
        return {"active": None, "message": "Workspace no disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workspaces/note")
async def add_workspace_note(request: Request, token: Optional[str] = None):
    """Añade una nota al workspace activo."""
    data = await request.json()
    note = data.get("note", "").strip()
    workspace = data.get("workspace")
    if not note:
        raise HTTPException(status_code=400, detail="note requerido")
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.workspace:
            ok = orchestrator.workspace.add_note(note, workspace)
            return {"ok": ok}
        return {"ok": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workspaces/task")
async def add_workspace_task(request: Request, token: Optional[str] = None):
    """Añade una tarea al workspace activo."""
    data = await request.json()
    task = data.get("task", "").strip()
    workspace = data.get("workspace")
    if not task:
        raise HTTPException(status_code=400, detail="task requerido")
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.workspace:
            task_id = orchestrator.workspace.add_task(task, workspace)
            return {"ok": True, "id": task_id}
        return {"ok": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workspaces/task/{task_id}/complete")
async def complete_workspace_task(request: Request, task_id: int, token: Optional[str] = None):
    """Marca una tarea como completada."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.workspace:
            ok = orchestrator.workspace.complete_task(task_id)
            return {"ok": ok}
        return {"ok": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/workspaces/{name}")
async def delete_workspace(request: Request, name: str, session: dict = Depends(require_admin)):
    """Elimina un workspace."""
    try:
        orchestrator = get_orchestrator(request)
        if orchestrator.workspace:
            ok = orchestrator.workspace.delete_workspace(name)
            return {"ok": ok}
        return {"ok": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
