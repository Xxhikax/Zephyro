"""Fitness router — endpoints REST para gestión de planes y sesiones de entrenamiento.

Sigue el patrón exacto de routers/schedule.py:
  - APIRouter() sin prefix
  - session derivada de Depends(require_auth)
  - user_id derivado de session.get("role", "admin")

Endpoints:
  POST   /fitness/plan/generate
  GET    /fitness/plan/current
  POST   /fitness/session/log
  POST   /fitness/session/verify    (multipart con UploadFile opcional)
  GET    /fitness/stats
  POST   /fitness/upgrade
  POST   /fitness/preferences
  GET    /fitness/session/{id}/alternatives
  POST   /fitness/session/{id}/reschedule
"""
from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.auth_middleware import require_auth

router = APIRouter()

from core.paths import UPLOAD_DIR


def _get_service():
    from modules.fitness.service import get_fitness_service
    return get_fitness_service()


# ── Pydantic models ──────────────────────────────────────────────────

class GeneratePlanBody(BaseModel):
    user_id: Optional[str] = None
    context: Optional[str] = "home"
    force: bool = False


class LogSessionBody(BaseModel):
    session_id: int
    notes: Optional[str] = ""
    verification_method: Optional[str] = "self_report"


class UpgradeBody(BaseModel):
    user_id: Optional[str] = None
    new_context: str


class PreferencesBody(BaseModel):
    user_id: Optional[str] = None
    preferred_days: Optional[list[int]] = None
    preferred_time_window: Optional[str] = None
    preferred_start_hour: Optional[int] = None
    min_rest_hours_between: Optional[int] = None


class RescheduleBody(BaseModel):
    new_start_ts_utc: Optional[float] = None
    reason: Optional[str] = "manual"


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/fitness/plan/generate")
async def generate_plan(body: GeneratePlanBody, session: dict = Depends(require_auth)):
    """Genera un nuevo plan de entrenamiento y lo agenda automáticamente."""
    user_id = body.user_id or session.get("role", "admin")
    svc = _get_service()

    existing = svc.get_active_plan(user_id)
    if existing and not body.force:
        return {
            "plan": existing,
            "message": "Ya tenés un plan activo. Usá force=true para regenerar.",
            "already_exists": True,
        }

    try:
        plan = await svc.generate_home_plan(user_id=user_id, context=body.context or "home")
        agenda_result = svc.materialize_plan_to_agenda(plan, user_id=user_id, weeks=4)
        return {
            "plan": plan,
            "agenda": agenda_result,
            "message": (
                f"Plan generado: {plan.get('raw', {}).get('weekly_frequency', '?')} sesiones/semana. "
                f"{agenda_result.get('scheduled', 0)} sesiones agendadas."
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error generando plan: {exc}")


@router.get("/fitness/plan/current")
async def get_current_plan(session: dict = Depends(require_auth), user_id: Optional[str] = None):
    """Devuelve el plan activo del usuario."""
    uid = user_id or session.get("role", "admin")
    plan = _get_service().get_active_plan(uid)
    if not plan:
        return {"plan": None, "message": "No tenés un plan activo. Generá uno con /fitness/plan/generate."}
    return {"plan": plan}


@router.post("/fitness/session/log")
async def log_session(body: LogSessionBody, session: dict = Depends(require_auth)):
    """Marca una sesión como completada."""
    svc = _get_service()
    result = svc.log_session(
        session_id=body.session_id,
        notes=body.notes or "",
        verification_method=body.verification_method or "self_report",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Error al loggear sesión"))
    return result


@router.post("/fitness/session/verify")
async def verify_session(
    session: dict = Depends(require_auth),
    session_id: int = Form(...),
    message: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    """Verifica evidencia de sesión. Acepta foto opcional (multipart).
    Auth via Authorization/X-Token header (Depends require_auth); token Form param removed (auth-header-migration REQ-3).
    """
    svc = _get_service()

    temp_file = None
    try:
        if file and file.filename:
            ext = Path(file.filename).suffix or ".jpg"
            temp_file = UPLOAD_DIR / f"fitness_verify_{secrets.token_hex(8)}{ext}"
            with temp_file.open("wb") as buf:
                shutil.copyfileobj(file.file, buf)

        score, counts = await svc.verify_session(
            session_id=session_id,
            message=message,
            image_path=str(temp_file) if temp_file else None,
        )

        if counts:
            svc.log_session(
                session_id=session_id,
                notes=message,
                verification_method="photo" if temp_file else "message",
            )

        return {
            "session_id": session_id,
            "score": round(score, 2),
            "counts": counts,
            "message": (
                f"Verificado! Score: {score:.0%}. Sesión registrada como completada."
                if counts
                else f"Score insuficiente ({score:.0%}). Mínimo requerido: 70%."
            ),
        }
    finally:
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


@router.get("/fitness/stats")
async def get_stats(session: dict = Depends(require_auth), user_id: Optional[str] = None):
    """Devuelve estadísticas y resumen semanal."""
    uid = user_id or session.get("role", "admin")
    svc = _get_service()
    streak = svc.get_streak(uid)
    summary = svc.get_weekly_summary(uid)
    profile = svc.get_profile(uid)
    return {
        "streak": streak,
        "weekly_summary": summary,
        "profile": profile,
    }


@router.post("/fitness/upgrade")
async def upgrade_plan(body: UpgradeBody, session: dict = Depends(require_auth)):
    """Cambia el contexto del plan (home/gym/muay_thai/swimming) y regenera."""
    uid = body.user_id or session.get("role", "admin")
    try:
        plan = await _get_service().upgrade_plan(uid, body.new_context)
        return {"plan": plan, "new_context": body.new_context}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error actualizando plan: {exc}")


@router.post("/fitness/preferences")
async def update_preferences(body: PreferencesBody, session: dict = Depends(require_auth)):
    """Actualiza preferencias de entrenamiento (días, horario, descanso)."""
    uid = body.user_id or session.get("role", "admin")
    svc = _get_service()

    kwargs = {}
    if body.preferred_days is not None:
        kwargs["preferred_days"] = [d for d in body.preferred_days if 0 <= d <= 6]
    if body.preferred_time_window is not None:
        valid_windows = {"morning", "afternoon", "evening", "any"}
        if body.preferred_time_window not in valid_windows:
            raise HTTPException(status_code=400, detail=f"preferred_time_window debe ser: {valid_windows}")
        kwargs["preferred_time_window"] = body.preferred_time_window
    if body.preferred_start_hour is not None:
        if not 0 <= body.preferred_start_hour <= 23:
            raise HTTPException(status_code=400, detail="preferred_start_hour debe ser 0-23")
        kwargs["preferred_start_hour"] = body.preferred_start_hour
    if body.min_rest_hours_between is not None:
        kwargs["min_rest_hours_between"] = max(0, body.min_rest_hours_between)

    profile = svc.update_profile(uid, **kwargs)
    return {"profile": profile, "updated": list(kwargs.keys())}


@router.get("/fitness/session/{session_id}/alternatives")
async def get_alternatives(
    session_id: int,
    session: dict = Depends(require_auth),
    n: int = 3,
):
    """Devuelve slots libres alternativos para una sesión."""
    svc = _get_service()
    alternatives = svc.suggest_alternative_slots(session_id, n=n)
    return {
        "session_id": session_id,
        "alternatives": alternatives,
        "count": len(alternatives),
    }


@router.post("/fitness/session/{session_id}/reschedule")
async def reschedule_session(
    session_id: int,
    body: RescheduleBody,
    session: dict = Depends(require_auth),
):
    """Reprograma una sesión (automático al primer slot disponible, o al ts indicado)."""
    svc = _get_service()

    if body.new_start_ts_utc:
        # Reprogramación manual a un timestamp específico
        fitness_session = svc._get_session(session_id)
        if not fitness_session:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        schedule_svc = svc._get_schedule_service()
        old_event_id = fitness_session.get("schedule_event_id")
        if old_event_id:
            schedule_svc.mark_cancelled(old_event_id)

        from datetime import datetime, timezone
        new_event = schedule_svc.create_event(
            user_id=fitness_session["user_id"],
            title=f"Entrenamiento: {fitness_session['session_type'].replace('_', ' ').title()} (reprogramado)",
            category="fitness",
            start_ts_utc=body.new_start_ts_utc,
            end_ts_utc=body.new_start_ts_utc + fitness_session["duration_min"] * 60,
            notes=f"Reprogramado: {body.reason}.",
            reminder_profile="fitness",
        )
        return {"session_id": session_id, "new_event_id": new_event.id, "reason": body.reason}
    else:
        # Auto-reprogramación
        result = await svc.auto_reschedule_session(session_id, reason=body.reason or "manual")
        if not result:
            raise HTTPException(status_code=404, detail="No hay slots disponibles para reprogramar")
        return result
