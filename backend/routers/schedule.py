"""Schedule router — endpoints REST para gestión de eventos de agenda."""
import re
from datetime import datetime, time as dtime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from core.auth_middleware import require_auth
from modules.schedule.service import get_schedule_service

router = APIRouter()

_MAX_TITLE_LEN = 200
_MAX_NOTES_LEN = 1000


def _get_service():
    return get_schedule_service()


_VALID_RECURRENCE = re.compile(
    r"^(daily|weekly:[A-Z]{3}(,[A-Z]{3})*)$"
)

RECURRENCE_WEEKS_DEFAULT = 16


class CreateEventBody(BaseModel):
    title: str
    category: str = "other"
    start_iso: str
    end_iso: Optional[str] = None
    notes: str = ""
    recurrence: Optional[str] = None

    @field_validator("recurrence")
    @classmethod
    def validate_recurrence(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if not _VALID_RECURRENCE.match(v):
                raise ValueError(f"invalid recurrence format: '{v}'")
        return v

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title is required")
        if len(v) > _MAX_TITLE_LEN:
            raise ValueError(f"title must be under {_MAX_TITLE_LEN} characters")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: str) -> str:
        if len(v) > _MAX_NOTES_LEN:
            raise ValueError(f"notes must be under {_MAX_NOTES_LEN} characters")
        return v


class UpdateEventBody(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    start_iso: Optional[str] = None
    end_iso: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("title cannot be empty")
            if len(v) > _MAX_TITLE_LEN:
                raise ValueError(f"title must be under {_MAX_TITLE_LEN} characters")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > _MAX_NOTES_LEN:
            raise ValueError(f"notes must be under {_MAX_NOTES_LEN} characters")
        return v


def _parse_iso(value: str) -> float:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid ISO datetime '{value}': {exc}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@router.get("/schedule/events")
async def list_events(
    request: Request,
    session: dict = Depends(require_auth),
    from_: Optional[str] = None,
    to: Optional[str] = None,
):
    user_id = session.get("role", "guest")

    from_ts = _parse_iso(from_) if from_ else datetime.combine(
        datetime.now().date(), dtime.min, tzinfo=timezone.utc
    ).timestamp()
    to_ts = _parse_iso(to) if to else from_ts + 7 * 86400

    events = _get_service().get_events_in_range(user_id, from_ts, to_ts)
    return {"events": [e.to_dict() for e in events]}


@router.post("/schedule/events")
async def create_event(
    request: Request,
    body: CreateEventBody,
    session: dict = Depends(require_auth),
):
    user_id = session.get("role", "guest")

    start_ts = _parse_iso(body.start_iso)
    end_ts = _parse_iso(body.end_iso) if body.end_iso else start_ts + 3600

    if end_ts <= start_ts:
        raise HTTPException(status_code=400, detail="end_iso must be after start_iso")

    try:
        svc = _get_service()
        if body.recurrence:
            series = svc.create_recurring_series(
                user_id=user_id,
                title=body.title,
                category=body.category,
                start_ts_utc=start_ts,
                end_ts_utc=end_ts,
                recurrence=body.recurrence,
                notes=body.notes,
                weeks=RECURRENCE_WEEKS_DEFAULT,
            )
            return {"events": [e.to_dict() for e in series], "recurring": True, "count": len(series)}
        event = svc.create_event(
            user_id=user_id,
            title=body.title,
            category=body.category,
            start_ts_utc=start_ts,
            end_ts_utc=end_ts,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"event": event.to_dict()}


@router.put("/schedule/events/{event_id}")
async def update_event(
    request: Request,
    event_id: int,
    body: UpdateEventBody,
    session: dict = Depends(require_auth),
):
    if all(v is None for v in [body.title, body.category, body.start_iso, body.end_iso, body.notes]):
        raise HTTPException(status_code=400, detail="No fields to update")

    start_ts = _parse_iso(body.start_iso) if body.start_iso else None
    end_ts = _parse_iso(body.end_iso) if body.end_iso else None

    if start_ts is not None and end_ts is not None and end_ts <= start_ts:
        raise HTTPException(status_code=400, detail="end_iso must be after start_iso")

    event = _get_service().update_event(
        event_id=event_id,
        title=body.title,
        category=body.category,
        start_ts_utc=start_ts,
        end_ts_utc=end_ts,
        notes=body.notes,
    )

    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return {"event": event.to_dict()}


@router.get("/schedule/check-overlap")
async def check_overlap(
    request: Request,
    session: dict = Depends(require_auth),
    start_iso: str = None,
    end_iso: str = None,
    exclude_id: Optional[int] = None,
):
    """Retorna los eventos que se solapan con el rango dado (para advertir antes de crear/editar)."""
    user_id = session.get("role", "guest")

    start_ts = _parse_iso(start_iso)
    end_ts = _parse_iso(end_iso)

    if end_ts <= start_ts:
        raise HTTPException(status_code=400, detail="end_iso must be after start_iso")

    conflicting = _get_service().check_overlap(user_id, start_ts, end_ts, exclude_id=exclude_id)
    return {
        "has_overlap": len(conflicting) > 0,
        "conflicts": [e.to_dict() for e in conflicting],
    }


@router.delete("/schedule/events/{event_id}")
async def delete_event(
    request: Request,
    event_id: int,
    session: dict = Depends(require_auth),
):
    ok = _get_service().delete_event(event_id)
    if not ok:
        raise HTTPException(status_code=404, detail="event not found")
    return {"deleted": True, "id": event_id}


class PatchEventStatus(BaseModel):
    status: str  # "completed" | "cancelled" | "pending"


@router.patch("/schedule/events/{event_id}/status")
async def patch_event_status(
    request: Request,
    event_id: int,
    body: PatchEventStatus,
    session: dict = Depends(require_auth),
):
    svc = _get_service()
    if body.status == "completed":
        ok = svc.mark_completed(event_id, completed=True)
    elif body.status == "cancelled":
        ok = svc.mark_cancelled(event_id)
    elif body.status == "pending":
        ok = svc.mark_completed(event_id, completed=False)
    else:
        raise HTTPException(status_code=400, detail=f"invalid status: {body.status}")
    if not ok:
        raise HTTPException(status_code=404, detail="event not found")
    event = svc.get_event_by_id(event_id)
    return {"event": event.to_dict() if event else None}


@router.get("/schedule/free-slots")
async def list_free_slots(
    request: Request,
    session: dict = Depends(require_auth),
    date: str = None,
    duration: int = 60,
):
    user_id = session.get("role", "guest")
    slots = _get_service().find_free_slots(user_id, date, duration_minutes=duration)
    return {"date": date, "duration_minutes": duration, "slots": slots}


# ── Focus mode ─────────────────────────────────────────────────────────

from modules.schedule.monitor import get_focus_monitor, generate_daily_checkin


@router.post("/focus/start")
async def start_focus(request: Request, session: dict = Depends(require_auth)):
    msg = get_focus_monitor().start()
    return {"active": True, "message": msg}


@router.post("/focus/stop")
async def stop_focus(request: Request, session: dict = Depends(require_auth)):
    msg = get_focus_monitor().stop()
    return {"active": False, "message": msg}


@router.get("/focus/status")
async def focus_status(request: Request, session: dict = Depends(require_auth)):
    msg = get_focus_monitor().status()
    return {"active": get_focus_monitor().active, "message": msg}


class JarvisCallModeBody(BaseModel):
    active: bool


@router.post("/focus/jarvis-call-mode")
async def set_jarvis_call_mode(
    body: JarvisCallModeBody,
    request: Request,
    session: dict = Depends(require_auth),
):
    """Signal from the frontend so the distraction classifier knows to relax
    messaging-app grace while the user is on a call with Zephyro."""
    get_focus_monitor().set_jarvis_call_mode(body.active)
    return {"jarvis_call_active": body.active}


# ── Pomodoro ────────────────────────────────────────────────────────────

class PomodoroStartRequest(BaseModel):
    task: str = ""
    cycles: int = 4
    work_min: int = 25
    break_min: int = 5
    strict_focus: bool = False  # vigilancia por visión (Haiku); off por defecto


@router.post("/pomodoro/start")
async def start_pomodoro(
    body: PomodoroStartRequest,
    request: Request,
    session: dict = Depends(require_auth),
):
    monitor = get_focus_monitor()
    msg = await monitor.start_pomodoro(
        task_name=body.task,
        duration_min=body.work_min,
        total_cycles=body.cycles,
        short_break_min=body.break_min,
        strict_focus=body.strict_focus,
    )
    return {"active": True, "message": msg}


@router.post("/pomodoro/stop")
async def stop_pomodoro(request: Request, session: dict = Depends(require_auth)):
    msg = await get_focus_monitor().stop_pomodoro()
    return {"active": False, "message": msg}


@router.post("/pomodoro/pause")
async def pause_pomodoro(request: Request, session: dict = Depends(require_auth)):
    msg = await get_focus_monitor().pause_pomodoro()
    return {"paused": True, "message": msg}


@router.post("/pomodoro/resume")
async def resume_pomodoro(request: Request, session: dict = Depends(require_auth)):
    msg = await get_focus_monitor().resume_pomodoro()
    return {"paused": False, "message": msg}


class PomodoroExtendRequest(BaseModel):
    minutes: int = 5
    mode: str = "current"  # "current" | "new_cycle"


@router.post("/pomodoro/extend")
async def extend_pomodoro(
    body: PomodoroExtendRequest,
    request: Request,
    session: dict = Depends(require_auth),
):
    if body.mode not in ("current", "new_cycle"):
        raise HTTPException(status_code=400, detail=f"invalid mode: {body.mode}")
    msg = await get_focus_monitor().extend_pomodoro(body.minutes, mode=body.mode)
    return {"message": msg}


@router.post("/pomodoro/skip-break")
async def skip_break(request: Request, session: dict = Depends(require_auth)):
    msg = await get_focus_monitor().skip_break()
    return {"message": msg}


@router.get("/pomodoro/stats")
async def pomodoro_stats(request: Request, session: dict = Depends(require_auth)):
    """Estadísticas de Pomodoros completados (hoy / total / minutos hoy)."""
    return get_focus_monitor().get_pomodoro_stats()


@router.get("/pomodoro/status")
async def pomodoro_status(request: Request, session: dict = Depends(require_auth)):
    monitor = get_focus_monitor()
    task = monitor._pomodoro_task
    active = bool(task and not task.done())
    remaining_sec = monitor._pomodoro_remaining_min() * 60 if active else 0
    # Duración TOTAL de la fase actual y su inicio real (epoch). El frontend los
    # usa para rehidratar el countdown sin colapsar total↔restante: con total_sec
    # + started_at reconstruye exactamente lo mismo que el broadcast WS 'start'.
    # _pomodoro_start_ts ya viene ajustado por pausas (pause lo empuja adelante).
    total_sec = (
        int(monitor._current_phase_duration_sec() + monitor._phase_extend_sec)
        if active else 0
    )
    return {
        "active": active,
        "phase": monitor._pomodoro_phase,
        "phase_detail": monitor._pomodoro_phase,
        "cycle": monitor._cycles_completed + 1 if active else 0,
        "total_cycles": monitor._total_cycles,
        "remaining_sec": remaining_sec,
        "total_sec": total_sec,
        "started_at": monitor._pomodoro_start_ts if active else 0,
        "paused": monitor._pomodoro_paused,
        "task": monitor._pomodoro_task_name,
        "message": monitor.status(),
    }


@router.get("/schedule/checkin")
async def daily_checkin(request: Request, session: dict = Depends(require_auth)):
    user_id = session.get("role", "guest")
    pending = _get_service().get_pending_today(user_id)
    events_list = [
        {"id": e.id, "title": e.title, "category": e.category}
        for e in pending
    ]
    msg = await generate_daily_checkin(events_list)
    return {"pending_count": len(pending), "message": msg, "events": events_list}
