"""Self state router — pseudo-consciousness, bug reports, reflection endpoints."""
import time

from fastapi import APIRouter, Depends, Request

from core.auth_middleware import require_admin

router = APIRouter()


def get_orchestrator(request: Request):
    return request.app.state.orchestrator


@router.get("/self/state")
async def get_self_state(request: Request, session: dict = Depends(require_admin)):
    """Obtener el estado interno de Zephyro"""
    orchestrator = get_orchestrator(request)
    try:
        state = orchestrator.self_state.export_state()
        return {"success": True, "state": state}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/self/bugs")
async def get_bug_reports(request: Request, session: dict = Depends(require_admin)):
    """Obtener bug reports del sistema de pseudo-consciencia"""
    orchestrator = get_orchestrator(request)
    try:
        summary = orchestrator.self_state.get_bug_reports_summary()
        return {"success": True, "bugs": summary}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/self/reflect")
async def trigger_reflection(request: Request, session: dict = Depends(require_admin), model_choice: str = ""):
    """Ejecutar reflexión bajo demanda. model_choice: '' | '1' | '2'"""
    orchestrator = get_orchestrator(request)
    try:
        start_time = time.perf_counter()
        result = await orchestrator._handle_reflect_command(model_choice)
        elapsed = time.perf_counter() - start_time
        return {"success": True, "reflection": result, "elapsed_time": round(elapsed, 2)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/self/bugs/clear")
async def clear_bug_reports(request: Request, session: dict = Depends(require_admin)):
    """Limpiar bug reports después de revisión"""
    orchestrator = get_orchestrator(request)
    try:
        orchestrator.self_state.clear_bug_reports()
        return {"success": True, "message": "Bug reports limpiados correctamente"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/self/living")
async def get_living_state(request: Request, session: dict = Depends(require_admin), tier: int = 2):
    """Snapshot del estado vivo del sistema (salud de skills, issues, cambios recientes)"""
    orchestrator = get_orchestrator(request)
    try:
        snapshot = orchestrator.consciousness.get_snapshot(tier=tier)
        return {"success": True, "snapshot": snapshot, "tier": tier}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/self/introspect")
async def get_introspection(request: Request, session: dict = Depends(require_admin)):
    """Introspección honesta: issues activos, skills rotas, cambios recientes, guards fallidos"""
    orchestrator = get_orchestrator(request)
    try:
        report = orchestrator.consciousness.introspect()
        return {"success": True, "introspection": report}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/self/capacidades")
async def get_capacidades(request: Request, session: dict = Depends(require_admin)):
    """Lista de capacidades activas de Zephyro en formato legible"""
    orchestrator = get_orchestrator(request)
    try:
        ss = orchestrator.self_state
        lines = [f"**{key}**: {cap['description']}" for key, cap in ss.capabilities.items()]
        skills_loaded = []
        try:
            for skill in orchestrator.skill_registry.get_all_skills():
                if skill.enabled:
                    skills_loaded.append(f"  - {skill.metadata.get('name', '?')}: {skill.metadata.get('description', '')}")
        except Exception:
            pass
        result = "## Capacidades de Zephyro\n\n"
        result += "\n".join(lines)
        if skills_loaded:
            result += "\n\n## Skills activas\n" + "\n".join(skills_loaded)
        result += f"\n\n_Sesión: {ss.session_stats['interactions_count']} interacciones_"
        return {"success": True, "capacidades": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
