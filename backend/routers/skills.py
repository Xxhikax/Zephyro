"""
Router para gestión de skills via API REST.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from core.auth_middleware import require_admin

router = APIRouter(tags=["skills"])


def _get_registry(request: Request):
    orch = getattr(request.app.state, "orchestrator", None)
    if not orch:
        return None
    return getattr(orch, "skill_registry", None)


@router.get("/skills")
async def list_skills(request: Request):
    """Lista todas las skills con metadata."""
    reg = _get_registry(request)
    if not reg:
        return JSONResponse({"error": "Skills registry not available"}, status_code=500)

    skills = []
    for s in reg.get_all():
        skills.append({
            "name": s.metadata.name,
            "description": s.metadata.description,
            "enabled": s.metadata.enabled,
            "triggers": s.metadata.triggers,
            "requires": s.metadata.requires,
        })
    return {"skills": skills, "total": len(skills)}


# ── Skill drafts (Parte B — auto-generadas) ─────────────────────────────
# IMPORTANTE: estos endpoints deben declararse ANTES de /skills/{name}
# para que el router de FastAPI los matchee primero. Los endpoints con
# `{name}` capturan cualquier path no-específico y devolverían 404.

@router.get("/skills/drafts")
async def list_drafts(_admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Lista drafts pendientes de aprobación."""
    from modules.learning.skill_proposer import get_skill_proposer
    proposer = get_skill_proposer()
    return {"drafts": proposer.list_pending()}


@router.post("/skills/drafts/{slug}/approve")
async def approve_draft(
    slug: str, _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Aprueba un draft: lo mueve de PENDING a community/."""
    from modules.learning.skill_proposer import get_skill_proposer
    proposer = get_skill_proposer()
    result = proposer.approve(slug)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "approve failed"))
    return result


@router.post("/skills/drafts/{slug}/reject")
async def reject_draft(
    slug: str, _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Rechaza un draft: lo elimina del directorio PENDING."""
    from modules.learning.skill_proposer import get_skill_proposer
    proposer = get_skill_proposer()
    result = proposer.reject(slug)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "reject failed"))
    return result


@router.get("/skills/drafts/{slug}/preview")
async def preview_draft(
    slug: str, _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Vista previa de un draft con codigo Python completo."""
    from modules.learning.skill_proposer import get_skill_proposer
    proposer = get_skill_proposer()
    drafts = proposer.list_pending()
    draft = next((d for d in drafts if d.get("slug") == slug), None)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft no encontrado")
    return {
        "slug": slug,
        "name": draft.get("name"),
        "description": draft.get("description"),
        "risk_level": draft.get("risk_level"),
        "triggers": draft.get("triggers", []),
        "uses_tools": draft.get("uses_tools", []),
        "skill_py": draft.get("skill_py", ""),
        "skill_md_body": draft.get("skill_md_body", ""),
        "lint_ok": draft.get("lint_ok"),
        "lint_summary": draft.get("lint_summary"),
    }


@router.post("/skills/proposer/nudge")
async def trigger_nudge(
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Dispara skill_proposer.nudge() manualmente (para debug/testing)."""
    from modules.learning.skill_proposer import get_skill_proposer
    proposer = get_skill_proposer()
    result = await proposer.nudge()
    return result


@router.get("/skills/{name}")
async def get_skill(name: str, request: Request):
    """Detalle de una skill."""
    reg = _get_registry(request)
    if not reg:
        return JSONResponse({"error": "Skills registry not available"}, status_code=500)

    skill = reg.get_skill(name)
    if not skill:
        return JSONResponse({"error": f"Skill '{name}' not found"}, status_code=404)

    ok, missing = skill.check_requirements()
    return {
        "name": skill.metadata.name,
        "description": skill.metadata.description,
        "enabled": skill.metadata.enabled,
        "triggers": skill.metadata.triggers,
        "requires": skill.metadata.requires,
        "provides": skill.metadata.provides,
        "requirements_met": ok,
        "missing_requirements": missing,
        "full_context": skill.get_full_context(),
    }


@router.post("/skills/{name}/enable")
async def enable_skill(name: str, request: Request):
    """Habilita una skill."""
    reg = _get_registry(request)
    if not reg:
        return JSONResponse({"error": "Skills registry not available"}, status_code=500)

    skill = reg.get_skill(name)
    if not skill:
        return JSONResponse({"error": f"Skill '{name}' not found"}, status_code=404)

    skill.metadata.enabled = True
    reg._rebuild_command_map()
    return {"status": "enabled", "skill": name}


@router.post("/skills/{name}/disable")
async def disable_skill(name: str, request: Request):
    """Deshabilita una skill."""
    reg = _get_registry(request)
    if not reg:
        return JSONResponse({"error": "Skills registry not available"}, status_code=500)

    skill = reg.get_skill(name)
    if not skill:
        return JSONResponse({"error": f"Skill '{name}' not found"}, status_code=404)

    skill.metadata.enabled = False
    reg._rebuild_command_map()
    return {"status": "disabled", "skill": name}
