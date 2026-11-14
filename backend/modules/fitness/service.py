"""FitnessService — lógica de negocio para rutinas, agenda y racha.

Singleton accesible via get_fitness_service().

Métodos públicos:
  get_profile / update_profile / set_preferred_days
  async generate_home_plan(user_id, refine_window_days=14)
  get_active_plan / archive_plan
  async upgrade_plan(user_id, new_context)
  materialize_plan_to_agenda(plan, weeks=4)
  async auto_reschedule_session(session_id, reason)
  async check_and_resolve_conflicts(user_id)
  suggest_alternative_slots(session_id, n=3)
  log_session(session_id, notes, verification_method)
  async verify_session(session_id, message, image_path=None)
  get_streak(user_id)
  get_weekly_summary(user_id)
  async maybe_refine_plan(user_id)
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, date, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("zephyro.fitness.service")

# Timezone Lima (UTC-5)
try:
    from zoneinfo import ZoneInfo
    _LIMA = ZoneInfo("America/Lima")
except ImportError:
    import pytz
    _LIMA = pytz.timezone("America/Lima")

# Fallback hardcoded plan si el LLM falla completamente
_FALLBACK_PLAN = {
    "weekly_frequency": 3,
    "rationale": "Plan mínimo de emergencia — regeneralo cuando tengas conexión.",
    "days": [
        {
            "day_of_week": 0,
            "session_type": "bag_combos",
            "duration_min": 45,
            "equipment": ["heavy_bag"],
            "blocks": [
                {"name": "Calentamiento", "duration_min": 5, "description": "Soga suave 3 min + shadowboxing 2 min."},
                {"name": "Bag combos", "duration_min": 30, "description": "5 rondas de 2 min: jab-cross-hook, uppercut, knee. 1 min descanso entre rondas."},
                {"name": "Vuelta calma", "duration_min": 5, "description": "Estiramientos de hombros, caderas y espalda."},
            ],
        },
        {
            "day_of_week": 2,
            "session_type": "soga_hiit",
            "duration_min": 40,
            "equipment": ["jump_rope"],
            "blocks": [
                {"name": "Calentamiento", "duration_min": 5, "description": "Soga lenta 3 min + movilidad articular 2 min."},
                {"name": "HIIT soga", "duration_min": 25, "description": "10 rondas: 30 seg soga rápido + 30 seg descanso."},
                {"name": "Core", "duration_min": 5, "description": "Plancha 3x30s + crunches 3x15."},
                {"name": "Vuelta calma", "duration_min": 5, "description": "Estiramientos completos."},
            ],
        },
        {
            "day_of_week": 4,
            "session_type": "shadowboxing",
            "duration_min": 45,
            "equipment": [],
            "blocks": [
                {"name": "Calentamiento", "duration_min": 5, "description": "Salta en el lugar 2 min + rotaciones 3 min."},
                {"name": "Shadowboxing técnico", "duration_min": 30, "description": "6 rondas de 3 min: enfocate en footwork, guardias y combinaciones fluidas."},
                {"name": "Vuelta calma", "duration_min": 10, "description": "Yoga básico + respiración diafragmática."},
            ],
        },
    ],
}


class FitnessService:
    """Servicio principal de fitness. Instanciar via get_fitness_service()."""

    def __init__(self):
        self._db_ready = False
        self._schedule_service = None
        self._llm_manager = None
        self._image_analyzer = None
        self._channel_manager = None
        self._setup_db()

    # ── Internal setup ──────────────────────────────────────────────

    def _setup_db(self) -> None:
        try:
            from modules.fitness.db import ensure_schema
            ensure_schema()
            self._db_ready = True
        except Exception as exc:
            log.warning("[FitnessService] DB setup falló: %s", exc)

    def _get_schedule_service(self):
        if self._schedule_service is None:
            from modules.schedule.service import get_schedule_service
            self._schedule_service = get_schedule_service()
        return self._schedule_service

    def _get_llm(self):
        return self._llm_manager

    def _get_image_analyzer(self):
        return self._image_analyzer

    def set_llm_manager(self, llm: Any) -> None:
        self._llm_manager = llm

    def set_image_analyzer(self, analyzer: Any) -> None:
        self._image_analyzer = analyzer

    def set_channel_manager(self, cm: Any) -> None:
        self._channel_manager = cm

    # ── DB helpers ──────────────────────────────────────────────────

    def _get_cursor(self):
        from core.postgres_pool import get_cursor
        return get_cursor(autocommit=True)

    # ── Profile ─────────────────────────────────────────────────────

    def get_profile(self, user_id: str = "admin") -> dict:
        """Devuelve el perfil del usuario. Crea uno con defaults si no existe."""
        if not self._db_ready:
            return self._default_profile(user_id)
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT goal, context, available_equipment, fitness_level, "
                    "weekly_target, preferred_days, preferred_time_window, "
                    "preferred_start_hour, min_rest_hours_between, protected_categories, notes "
                    "FROM fitness_profile WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        "INSERT INTO fitness_profile (user_id) VALUES (%s) "
                        "ON CONFLICT (user_id) DO NOTHING",
                        (user_id,),
                    )
                    return self._default_profile(user_id)
                return {
                    "user_id": user_id,
                    "goal": row[0],
                    "context": row[1],
                    "available_equipment": list(row[2] or []),
                    "fitness_level": row[3],
                    "weekly_target": row[4],
                    "preferred_days": list(row[5] or []) if row[5] else None,
                    "preferred_time_window": row[6],
                    "preferred_start_hour": row[7],
                    "min_rest_hours_between": row[8],
                    "protected_categories": list(row[9] or []),
                    "notes": row[10],
                }
        except Exception as exc:
            log.warning("[FitnessService] get_profile error: %s", exc)
            return self._default_profile(user_id)

    def _default_profile(self, user_id: str) -> dict:
        return {
            "user_id": user_id,
            "goal": "body_recomp",
            "context": "home",
            "available_equipment": ["jump_rope", "heavy_bag", "space"],
            "fitness_level": "intermediate",
            "weekly_target": 4,
            "preferred_days": None,
            "preferred_time_window": "evening",
            "preferred_start_hour": 18,
            "min_rest_hours_between": 24,
            "protected_categories": [
                "university", "uni_exam", "uni_assignment",
                "course_study", "personal_project", "client_project",
            ],
            "notes": None,
        }

    def update_profile(self, user_id: str, **kwargs) -> dict:
        """Actualiza campos del perfil."""
        if not self._db_ready:
            return self._default_profile(user_id)
        allowed = {
            "goal", "context", "available_equipment", "fitness_level",
            "weekly_target", "preferred_days", "preferred_time_window",
            "preferred_start_hour", "min_rest_hours_between",
            "protected_categories", "notes",
        }
        sets = []
        values = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k} = %s")
                values.append(v)
        if not sets:
            return self.get_profile(user_id)
        sets.append("updated_at = NOW()")
        values.append(user_id)
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    f"INSERT INTO fitness_profile (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id,),
                )
                cur.execute(
                    f"UPDATE fitness_profile SET {', '.join(sets)} WHERE user_id = %s",
                    values,
                )
        except Exception as exc:
            log.warning("[FitnessService] update_profile error: %s", exc)
        return self.get_profile(user_id)

    def set_preferred_days(self, user_id: str, days: list[int]) -> dict:
        """Fija días preferidos de entrenamiento (0=lun .. 6=dom)."""
        days_clean = [d for d in days if 0 <= d <= 6]
        return self.update_profile(user_id, preferred_days=days_clean if days_clean else None)

    # ── Plan generation ─────────────────────────────────────────────

    async def generate_home_plan(
        self,
        user_id: str = "admin",
        refine_window_days: int = 14,
        refine_notes: str = "",
        context: str | None = None,
    ) -> dict:
        """Genera un plan de entrenamiento via LLM cloud (Claude Sonnet).
        Retry 2 veces. Fallback a template hardcoded si todo falla.
        Devuelve el dict del plan guardado (con id, status='active').
        """
        profile = self.get_profile(user_id)
        ctx = context or profile["context"]

        from modules.fitness.prompts import (
            system_prompt_for_context,
            generate_plan_user_prompt,
        )

        system = system_prompt_for_context(ctx)
        user_prompt = generate_plan_user_prompt(
            weekly_target=profile["weekly_target"],
            preferred_days=profile["preferred_days"],
            preferred_time_window=profile["preferred_time_window"],
            fitness_level=profile["fitness_level"],
            equipment=profile["available_equipment"],
            context=ctx,
            refine_notes=refine_notes,
        )

        raw_plan_data = None
        llm = self._get_llm()

        if llm:
            full_prompt = f"{system}\n\nUsuario: {user_prompt}"
            for attempt in range(3):
                try:
                    loop = asyncio.get_event_loop()
                    raw = await loop.run_in_executor(
                        None,
                        lambda: llm.generate(full_prompt, force_cloud=True),
                    )
                    from modules.fitness.schema import validate_plan_json
                    plan_obj = validate_plan_json(raw)
                    raw_plan_data = json.loads(raw.strip().lstrip("```json").lstrip("```").rstrip("```"))
                    raw_plan_data["_validated"] = True
                    break
                except Exception as exc:
                    log.warning("[FitnessService] generate attempt %d failed: %s", attempt + 1, exc)
                    await asyncio.sleep(1)

        if raw_plan_data is None:
            log.warning("[FitnessService] Usando plan fallback hardcodeado")
            raw_plan_data = _FALLBACK_PLAN.copy()

        # Archivar plan anterior si existe
        self._archive_current_plans(user_id)

        # Guardar nuevo plan
        plan_id = self._save_plan(
            user_id=user_id,
            context=ctx,
            weekly_frequency=raw_plan_data.get("weekly_frequency", len(raw_plan_data.get("days", []))),
            rationale=raw_plan_data.get("rationale", ""),
            raw=raw_plan_data,
            generated_by="llm_cloud",
        )

        return {"id": plan_id, "status": "active", "raw": raw_plan_data}

    def _archive_current_plans(self, user_id: str) -> None:
        if not self._db_ready:
            return
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "UPDATE workout_plans SET status='archived' WHERE user_id=%s AND status='active'",
                    (user_id,),
                )
        except Exception as exc:
            log.warning("[FitnessService] archive_current_plans: %s", exc)

    def _save_plan(
        self,
        user_id: str,
        context: str,
        weekly_frequency: int,
        rationale: str,
        raw: dict,
        generated_by: str = "llm_cloud",
    ) -> int:
        if not self._db_ready:
            return -1
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "INSERT INTO workout_plans "
                    "(user_id, context, status, weekly_frequency, rationale, raw, generated_by) "
                    "VALUES (%s, %s, 'active', %s, %s, %s, %s) RETURNING id",
                    (user_id, context, weekly_frequency, rationale, json.dumps(raw), generated_by),
                )
                row = cur.fetchone()
                return row[0] if row else -1
        except Exception as exc:
            log.warning("[FitnessService] _save_plan: %s", exc)
            return -1

    def get_active_plan(self, user_id: str = "admin") -> Optional[dict]:
        """Devuelve el plan activo o None."""
        if not self._db_ready:
            return None
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT id, context, weekly_frequency, rationale, raw, generated_at "
                    "FROM workout_plans WHERE user_id=%s AND status='active' "
                    "ORDER BY generated_at DESC LIMIT 1",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "context": row[1],
                    "weekly_frequency": row[2],
                    "rationale": row[3],
                    "raw": row[4],
                    "generated_at": row[5].isoformat() if row[5] else None,
                    "status": "active",
                }
        except Exception as exc:
            log.warning("[FitnessService] get_active_plan: %s", exc)
            return None

    def archive_plan(self, plan_id: int) -> bool:
        if not self._db_ready:
            return False
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "UPDATE workout_plans SET status='archived' WHERE id=%s",
                    (plan_id,),
                )
                return True
        except Exception as exc:
            log.warning("[FitnessService] archive_plan: %s", exc)
            return False

    async def upgrade_plan(self, user_id: str, new_context: str) -> dict:
        """Cambia el contexto del plan (home→gym→muay_thai→swimming) y regenera."""
        valid = {"home", "gym", "muay_thai", "swimming"}
        if new_context not in valid:
            raise ValueError(f"Contexto inválido: {new_context}. Opciones: {', '.join(valid)}")
        self.update_profile(user_id, context=new_context)
        return await self.generate_home_plan(user_id, context=new_context)

    # ── Auto-scheduling ──────────────────────────────────────────────

    def materialize_plan_to_agenda(
        self,
        plan: dict,
        user_id: str = "admin",
        weeks: int = 4,
    ) -> dict:
        """Crea ScheduleEvents para cada sesión del plan en las próximas `weeks` semanas.
        Respeta preferred_days, preferred_start_hour, min_rest_hours_between.
        Evita protected_categories.
        Retorna {"scheduled": int, "unscheduled": int, "sessions": list}.
        """
        profile = self.get_profile(user_id)
        svc = self._get_schedule_service()
        plan_raw = plan.get("raw", plan)
        plan_id = plan.get("id", -1)
        days_config = plan_raw.get("days", [])

        if not days_config:
            return {"scheduled": 0, "unscheduled": 0, "sessions": []}

        preferred_days: list[int] | None = profile.get("preferred_days")
        preferred_start_hour: int = profile.get("preferred_start_hour", 18)
        min_rest_h: int = profile.get("min_rest_hours_between", 24)
        duration_buf_min: int = 30  # buffer de 30 min extra al buscar slot

        now = datetime.now(timezone.utc)
        # Comenzamos desde mañana
        start_date = (now + timedelta(days=1)).date()

        scheduled: list[dict] = []
        unscheduled: list[dict] = []
        last_session_ts: float | None = None  # para respetar min_rest

        for week in range(weeks):
            for day_cfg in days_config:
                dow = int(day_cfg.get("day_of_week", 0))
                duration_min = int(day_cfg.get("duration_min", 45))
                session_type = day_cfg.get("session_type", "general")
                equipment = list(day_cfg.get("equipment") or [])
                blocks = list(day_cfg.get("blocks") or [])

                # Calcular la fecha objetivo para esta semana
                week_start = start_date + timedelta(weeks=week)
                # Próximo dow desde week_start
                delta = (dow - week_start.weekday()) % 7
                target_date = week_start + timedelta(days=delta)

                # Si hay preferred_days definidos, saltar días que no estén ahí
                if preferred_days and dow not in preferred_days:
                    # Buscar el día preferido más cercano en esa semana
                    found = False
                    for pd in preferred_days:
                        d2 = (pd - week_start.weekday()) % 7
                        candidate = week_start + timedelta(days=d2)
                        if candidate >= start_date:
                            target_date = candidate
                            found = True
                            break
                    if not found:
                        result = self._create_session_row(
                            plan_id=plan_id, user_id=user_id,
                            day_of_week=dow, session_type=session_type,
                            planned_at=datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc),
                            duration_min=duration_min, equipment=equipment, blocks=blocks,
                            status="unscheduled",
                        )
                        unscheduled.append(result)
                        continue

                date_str = target_date.strftime("%Y-%m-%d")
                slot = self._find_best_slot(
                    svc=svc,
                    user_id=user_id,
                    date_str=date_str,
                    duration_min=duration_min + duration_buf_min,
                    preferred_start_hour=preferred_start_hour,
                    last_session_ts=last_session_ts,
                    min_rest_h=min_rest_h,
                    protected_categories=profile.get("protected_categories", []),
                )

                if slot:
                    slot_start = slot["start"]
                    event_end = slot_start + duration_min * 60
                    event = svc.create_event(
                        user_id=user_id,
                        title=f"Entrenamiento: {session_type.replace('_', ' ').title()}",
                        category="fitness",
                        start_ts_utc=slot_start,
                        end_ts_utc=event_end,
                        notes=f"Sesión: {session_type}. Equipo: {', '.join(equipment) or 'sin equipo'}.",
                        reminder_profile="fitness",
                    )
                    last_session_ts = event_end
                    session_id = self._create_session_row(
                        plan_id=plan_id, user_id=user_id,
                        day_of_week=dow, session_type=session_type,
                        planned_at=datetime.fromtimestamp(slot_start, tz=timezone.utc),
                        duration_min=duration_min, equipment=equipment, blocks=blocks,
                        status="planned", schedule_event_id=event.id,
                    )
                    scheduled.append({"session_id": session_id, "event_id": event.id, "date": date_str})
                else:
                    # Intentar día siguiente
                    next_date = target_date + timedelta(days=1)
                    next_date_str = next_date.strftime("%Y-%m-%d")
                    slot2 = self._find_best_slot(
                        svc=svc, user_id=user_id, date_str=next_date_str,
                        duration_min=duration_min + duration_buf_min,
                        preferred_start_hour=preferred_start_hour,
                        last_session_ts=last_session_ts, min_rest_h=min_rest_h,
                        protected_categories=profile.get("protected_categories", []),
                    )
                    if slot2:
                        slot_start = slot2["start"]
                        event_end = slot_start + duration_min * 60
                        event = svc.create_event(
                            user_id=user_id,
                            title=f"Entrenamiento: {session_type.replace('_', ' ').title()}",
                            category="fitness",
                            start_ts_utc=slot_start,
                            end_ts_utc=event_end,
                            notes=f"Sesión: {session_type}. Equipo: {', '.join(equipment) or 'sin equipo'}.",
                            reminder_profile="fitness",
                        )
                        last_session_ts = event_end
                        session_id = self._create_session_row(
                            plan_id=plan_id, user_id=user_id,
                            day_of_week=dow, session_type=session_type,
                            planned_at=datetime.fromtimestamp(slot_start, tz=timezone.utc),
                            duration_min=duration_min, equipment=equipment, blocks=blocks,
                            status="planned", schedule_event_id=event.id,
                        )
                        scheduled.append({"session_id": session_id, "event_id": event.id, "date": next_date_str})
                    else:
                        planned_at = datetime.combine(target_date, datetime.min.time().replace(hour=preferred_start_hour), tzinfo=timezone.utc)
                        session_id = self._create_session_row(
                            plan_id=plan_id, user_id=user_id,
                            day_of_week=dow, session_type=session_type,
                            planned_at=planned_at,
                            duration_min=duration_min, equipment=equipment, blocks=blocks,
                            status="unscheduled",
                        )
                        unscheduled.append({"session_id": session_id, "date": date_str})

        return {
            "scheduled": len(scheduled),
            "unscheduled": len(unscheduled),
            "sessions": scheduled + unscheduled,
        }

    def _find_best_slot(
        self,
        svc,
        user_id: str,
        date_str: str,
        duration_min: int,
        preferred_start_hour: int,
        last_session_ts: float | None,
        min_rest_h: int,
        protected_categories: list[str],
    ) -> Optional[dict]:
        """Devuelve el mejor slot libre respetando preferencias."""
        # Buscar slots libres entre 6h y 23h
        slots = svc.find_free_slots(
            user_id=user_id,
            date_str=date_str,
            duration_minutes=duration_min,
            start_hour=6,
            end_hour=23,
        )
        if not slots:
            return None

        # Filtrar slots que coincidan con eventos de categorías protegidas
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
        day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        day_end = day_start + 86400
        events = svc.get_events_in_range(user_id, day_start, day_end)
        protected_busy = [
            (e.start_ts_utc, e.end_ts_utc)
            for e in events
            if e.category in protected_categories and e.status not in ("cancelled", "completed")
        ]

        valid_slots = []
        for slot in slots:
            slot_start = slot["start"]
            slot_end = slot_start + duration_min * 60

            # Verificar rest mínimo desde última sesión
            if last_session_ts and (slot_start - last_session_ts) < min_rest_h * 3600:
                continue

            # Verificar que no solape con categorías protegidas
            overlaps = any(
                slot_start < pb_end and slot_end > pb_start
                for pb_start, pb_end in protected_busy
            )
            if overlaps:
                continue

            valid_slots.append(slot)

        if not valid_slots:
            return None

        # Ordenar por proximidad al preferred_start_hour
        preferred_ts = day_start + preferred_start_hour * 3600

        def proximity(slot):
            return abs(slot["start"] - preferred_ts)

        valid_slots.sort(key=proximity)
        best = valid_slots[0]
        # Ajustar el slot al preferred_start_hour si cabe
        target_start = max(best["start"], preferred_ts)
        if target_start + duration_min * 60 <= best["end"]:
            return {"start": target_start, "end": best["end"]}
        return {"start": best["start"], "end": best["end"]}

    def _create_session_row(
        self,
        plan_id: int,
        user_id: str,
        day_of_week: int,
        session_type: str,
        planned_at: datetime,
        duration_min: int,
        equipment: list[str],
        blocks: list,
        status: str = "planned",
        schedule_event_id: Optional[int] = None,
    ) -> int:
        """Inserta una fila en workout_sessions. Retorna el id."""
        if not self._db_ready:
            return -1
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "INSERT INTO workout_sessions "
                    "(plan_id, schedule_event_id, user_id, day_of_week, session_type, "
                    "planned_at, duration_min, equipment, blocks, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        plan_id if plan_id != -1 else None,
                        schedule_event_id,
                        user_id,
                        day_of_week,
                        session_type,
                        planned_at,
                        duration_min,
                        equipment,
                        json.dumps(blocks),
                        status,
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else -1
        except Exception as exc:
            log.warning("[FitnessService] _create_session_row: %s", exc)
            return -1

    # ── Conflict resolution ─────────────────────────────────────────

    async def check_and_resolve_conflicts(self, user_id: str = "admin") -> None:
        """Callback para cuando llega un evento nuevo. Detecta solapamientos con sesiones fitness."""
        if not self._db_ready:
            return
        try:
            profile = self.get_profile(user_id)
            protected = set(profile.get("protected_categories", []))
            svc = self._get_schedule_service()

            # Obtener sesiones fitness planned de esta semana y la siguiente
            now = datetime.now(timezone.utc)
            week_start = now.timestamp()
            week_end = (now + timedelta(weeks=2)).timestamp()

            fitness_sessions = self._get_planned_sessions(user_id, week_start, week_end)
            if not fitness_sessions:
                return

            events = svc.get_events_in_range(user_id, week_start, week_end)
            new_protected = [
                e for e in events
                if e.category in protected and e.status not in ("cancelled", "completed")
            ]

            for session in fitness_sessions:
                s_start = session.get("planned_at_ts", 0)
                s_end = s_start + session.get("duration_min", 45) * 60
                for ev in new_protected:
                    if ev.start_ts_utc < s_end and ev.end_ts_utc > s_start:
                        # Conflicto detectado — sugerir slots
                        asyncio.create_task(
                            self._notify_conflict(user_id, session, ev)
                        )
                        break
        except Exception as exc:
            log.warning("[FitnessService] check_and_resolve_conflicts: %s", exc)

    async def _notify_conflict(self, user_id: str, session: dict, conflicting_event) -> None:
        """Pushea notificación de conflicto al usuario via Telegram."""
        try:
            alternatives = self.suggest_alternative_slots(session["id"], n=3)
            session_name = session.get("session_type", "entrenamiento").replace("_", " ")
            lima_dt = datetime.fromtimestamp(session.get("planned_at_ts", 0), tz=_LIMA)
            session_time = lima_dt.strftime("%A %d/%m %H:%M")

            msg = (
                f"Tu sesion '{session_name}' del {session_time} "
                f"choca con '{conflicting_event.title}'. "
            )
            if alternatives:
                opts = []
                for i, alt in enumerate(alternatives, 1):
                    alt_dt = datetime.fromtimestamp(alt["start"], tz=_LIMA)
                    opts.append(f"{i}) {alt_dt.strftime('%A %H:%M')}")
                msg += f"Sugiero moverla a: {', '.join(opts)}. Respondeme el numero o 'no'."
            else:
                msg += "No encontre slots libres esta semana. Considera reorganizar manualmente."

            if self._channel_manager:
                await self._channel_manager.broadcast(msg)
            else:
                log.info("[FitnessService] Conflicto detectado: %s", msg)
        except Exception as exc:
            log.warning("[FitnessService] _notify_conflict: %s", exc)

    def suggest_alternative_slots(self, session_id: int, n: int = 3) -> list[dict]:
        """Devuelve n slots libres alternativos para una sesión."""
        if not self._db_ready:
            return []
        session = self._get_session(session_id)
        if not session:
            return []

        profile = self.get_profile(session["user_id"])
        svc = self._get_schedule_service()
        planned_at_ts = session.get("planned_at_ts", time.time())
        duration_min = session.get("duration_min", 45)
        user_id = session["user_id"]

        alternatives = []
        for delta_days in range(1, 8):
            target = datetime.fromtimestamp(planned_at_ts, tz=timezone.utc) + timedelta(days=delta_days)
            date_str = target.strftime("%Y-%m-%d")
            slot = self._find_best_slot(
                svc=svc, user_id=user_id, date_str=date_str,
                duration_min=duration_min + 30,
                preferred_start_hour=profile.get("preferred_start_hour", 18),
                last_session_ts=None, min_rest_h=0,
                protected_categories=profile.get("protected_categories", []),
            )
            if slot:
                alternatives.append(slot)
            if len(alternatives) >= n:
                break
        return alternatives

    async def auto_reschedule_session(self, session_id: int, reason: str = "conflict") -> Optional[dict]:
        """Reprograma una sesión al primer slot alternativo disponible."""
        alternatives = self.suggest_alternative_slots(session_id, n=1)
        if not alternatives:
            return None
        slot = alternatives[0]
        session = self._get_session(session_id)
        if not session:
            return None

        svc = self._get_schedule_service()
        duration_min = session.get("duration_min", 45)
        session_type = session.get("session_type", "general")
        user_id = session["user_id"]
        equipment = session.get("equipment", [])

        # Cancelar evento anterior si existe
        old_event_id = session.get("schedule_event_id")
        if old_event_id:
            svc.mark_cancelled(old_event_id)

        # Crear nuevo evento
        new_event = svc.create_event(
            user_id=user_id,
            title=f"Entrenamiento: {session_type.replace('_', ' ').title()} (reprogramado)",
            category="fitness",
            start_ts_utc=slot["start"],
            end_ts_utc=slot["start"] + duration_min * 60,
            notes=f"Reprogramado por: {reason}.",
            reminder_profile="fitness",
        )

        # Actualizar sesión en DB
        if self._db_ready:
            try:
                with self._get_cursor() as cur:
                    cur.execute(
                        "UPDATE workout_sessions SET "
                        "schedule_event_id=%s, planned_at=%s, status='planned' "
                        "WHERE id=%s",
                        (new_event.id, datetime.fromtimestamp(slot["start"], tz=timezone.utc), session_id),
                    )
            except Exception as exc:
                log.warning("[FitnessService] auto_reschedule_session: %s", exc)

        lima_dt = datetime.fromtimestamp(slot["start"], tz=_LIMA)
        return {"session_id": session_id, "new_event_id": new_event.id, "new_start": lima_dt.isoformat()}

    # ── Session logging ─────────────────────────────────────────────

    def log_session(
        self,
        session_id: int,
        notes: str = "",
        verification_method: str = "self_report",
    ) -> dict:
        """Marca una sesión como completada y recalcula streak."""
        if not self._db_ready:
            return {"ok": False, "error": "DB no disponible"}
        try:
            now = datetime.now(timezone.utc)
            with self._get_cursor() as cur:
                cur.execute(
                    "UPDATE workout_sessions SET "
                    "status='completed', completed_at=%s, notes=%s, "
                    "verification_method=%s, points=10 "
                    "WHERE id=%s",
                    (now, notes, verification_method, session_id),
                )
            session = self._get_session(session_id)
            user_id = session["user_id"] if session else "admin"
            streak = self._recalculate_streak(user_id)
            return {"ok": True, "session_id": session_id, "streak": streak}
        except Exception as exc:
            log.warning("[FitnessService] log_session: %s", exc)
            return {"ok": False, "error": str(exc)}

    async def verify_session(
        self,
        session_id: int,
        message: str,
        image_path: Optional[str] = None,
    ) -> tuple[float, bool]:
        """Verifica evidencia de sesión completada. Retorna (score, counts)."""
        score = 0.0
        session = self._get_session(session_id)
        if not session:
            return 0.0, False

        session_type = session.get("session_type", "general")
        duration_min = session.get("duration_min", 45)
        equipment = list(session.get("equipment") or [])

        if image_path:
            analyzer = self._get_image_analyzer()
            if analyzer:
                from modules.fitness.prompts import verify_session_prompt
                prompt = verify_session_prompt(session_type, duration_min, equipment)
                try:
                    loop = asyncio.get_event_loop()
                    raw = await loop.run_in_executor(
                        None,
                        lambda: analyzer.analyze(image_path, prompt, use_cloud=True),
                    )
                    # Intentar parsear JSON del resultado
                    try:
                        start = raw.find("{")
                        end = raw.rfind("}") + 1
                        if start >= 0 and end > start:
                            data = json.loads(raw[start:end])
                            score = float(data.get("score", 0.0))
                        else:
                            # Heurística si no hay JSON
                            score = 0.5 if any(
                                w in raw.lower()
                                for w in ["sudor", "esfuerzo", "entrenamiento", "ejercicio"]
                            ) else 0.2
                    except Exception:
                        score = 0.4
                except Exception as exc:
                    log.warning("[FitnessService] verify_session vision: %s", exc)
                    score = 0.3
            else:
                score = 0.5  # Sin analizador, trust medio
        else:
            # Sin foto, el mensaje vale algo
            lower = message.lower()
            if any(w in lower for w in ["hice", "completé", "termine", "entrene", "sudé", "cansado"]):
                score = 0.6
            else:
                score = 0.4

        counts = score >= 0.70

        # Actualizar score en DB
        if self._db_ready:
            try:
                with self._get_cursor() as cur:
                    cur.execute(
                        "UPDATE workout_sessions SET verification_score=%s WHERE id=%s",
                        (score, session_id),
                    )
            except Exception:
                pass

        return score, counts

    # ── Streak ──────────────────────────────────────────────────────

    def _recalculate_streak(self, user_id: str) -> dict:
        """Recalcula racha basado en sesiones completadas."""
        if not self._db_ready:
            return {"current": 0, "longest": 0}
        try:
            with self._get_cursor() as cur:
                # Obtener fechas únicas de sesiones completadas
                cur.execute(
                    "SELECT DISTINCT DATE(completed_at AT TIME ZONE 'America/Lima') as d "
                    "FROM workout_sessions "
                    "WHERE user_id=%s AND status='completed' AND completed_at IS NOT NULL "
                    "ORDER BY d DESC",
                    (user_id,),
                )
                rows = cur.fetchall()
                if not rows:
                    cur.execute(
                        "UPDATE streaks SET current=0, updated_at=NOW() WHERE user_id=%s",
                        (user_id,),
                    )
                    return {"current": 0, "longest": 0}

                dates = [row[0] for row in rows]
                current = 1
                longest_run = 1
                for i in range(1, len(dates)):
                    diff = (dates[i - 1] - dates[i]).days
                    if diff == 1:
                        current += 1
                        longest_run = max(longest_run, current)
                    elif diff > 1:
                        current = 1

                # Verificar si la última sesión fue hoy o ayer (si fue antes, racha rota)
                today = date.today()
                last_date = dates[0]
                if (today - last_date).days > 1:
                    current = 0

                cur.execute(
                    "INSERT INTO streaks (user_id, current, longest, last_completed_date) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "current=EXCLUDED.current, "
                    "longest=GREATEST(streaks.longest, EXCLUDED.longest), "
                    "last_completed_date=EXCLUDED.last_completed_date, "
                    "updated_at=NOW()",
                    (user_id, current, longest_run, last_date),
                )
                return {"current": current, "longest": longest_run, "last_date": last_date.isoformat()}
        except Exception as exc:
            log.warning("[FitnessService] _recalculate_streak: %s", exc)
            return {"current": 0, "longest": 0}

    def get_streak(self, user_id: str = "admin") -> dict:
        """Devuelve el streak actual."""
        if not self._db_ready:
            return {"current": 0, "longest": 0}
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT current, longest, last_completed_date FROM streaks WHERE user_id=%s",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"current": 0, "longest": 0}
                return {
                    "current": row[0],
                    "longest": row[1],
                    "last_date": row[2].isoformat() if row[2] else None,
                }
        except Exception as exc:
            log.warning("[FitnessService] get_streak: %s", exc)
            return {"current": 0, "longest": 0}

    # ── Weekly summary ───────────────────────────────────────────────

    def get_weekly_summary(self, user_id: str = "admin") -> dict:
        """Resumen de la semana actual."""
        if not self._db_ready:
            return {"completed": 0, "planned": 0, "missed": 0, "streak": 0}
        now = datetime.now(timezone.utc)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_end = week_start + timedelta(weeks=1)

        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT status, COUNT(*) FROM workout_sessions "
                    "WHERE user_id=%s AND planned_at >= %s AND planned_at < %s "
                    "GROUP BY status",
                    (user_id, week_start, week_end),
                )
                rows = cur.fetchall()
                counts = {r[0]: r[1] for r in rows}

            streak = self.get_streak(user_id)
            return {
                "week_start": week_start.date().isoformat(),
                "completed": counts.get("completed", 0),
                "planned": counts.get("planned", 0),
                "missed": counts.get("missed", 0),
                "skipped": counts.get("skipped", 0),
                "streak": streak,
            }
        except Exception as exc:
            log.warning("[FitnessService] get_weekly_summary: %s", exc)
            return {"completed": 0, "planned": 0, "missed": 0, "streak": 0}

    # ── Plan refinement ─────────────────────────────────────────────

    async def maybe_refine_plan(self, user_id: str = "admin") -> Optional[dict]:
        """Refina el plan si hay suficiente historial (14+ días)."""
        if not self._db_ready:
            return None
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*), AVG(verification_score) FROM workout_sessions "
                    "WHERE user_id=%s AND planned_at > NOW() - INTERVAL '14 days' "
                    "AND status IN ('completed','missed')",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row or row[0] < 4:
                    return None  # Datos insuficientes

                total = row[0]
                avg_score = float(row[1] or 0.5)

            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM workout_sessions "
                    "WHERE user_id=%s AND status='completed' "
                    "AND planned_at > NOW() - INTERVAL '14 days'",
                    (user_id,),
                )
                completed = (cur.fetchone() or [0])[0]

            completion_rate = completed / total if total > 0 else 0
            profile = self.get_profile(user_id)
            notes = ""

            if completion_rate < 0.60:
                notes = f"Tasa de completado baja ({completion_rate:.0%}). Bajá frecuencia o duración."
            elif completion_rate > 0.85 and avg_score > 0.75:
                notes = f"Excelente adherencia ({completion_rate:.0%}, score={avg_score:.2f}). Subí intensidad."
            else:
                return None  # No hace falta refinar

            return await self.generate_home_plan(user_id, refine_notes=notes)
        except Exception as exc:
            log.warning("[FitnessService] maybe_refine_plan: %s", exc)
            return None

    # ── Internal helpers ─────────────────────────────────────────────

    def _get_session(self, session_id: int) -> Optional[dict]:
        if not self._db_ready:
            return None
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT id, plan_id, schedule_event_id, user_id, day_of_week, "
                    "session_type, EXTRACT(EPOCH FROM planned_at) as planned_at_ts, "
                    "duration_min, equipment, blocks, status "
                    "FROM workout_sessions WHERE id=%s",
                    (session_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "plan_id": row[1],
                    "schedule_event_id": row[2],
                    "user_id": row[3],
                    "day_of_week": row[4],
                    "session_type": row[5],
                    "planned_at_ts": float(row[6] or 0),
                    "duration_min": row[7],
                    "equipment": list(row[8] or []),
                    "blocks": row[9] if isinstance(row[9], list) else json.loads(row[9] or "[]"),
                    "status": row[10],
                }
        except Exception as exc:
            log.warning("[FitnessService] _get_session: %s", exc)
            return None

    def _get_planned_sessions(self, user_id: str, from_ts: float, to_ts: float) -> list[dict]:
        if not self._db_ready:
            return []
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT id, session_type, EXTRACT(EPOCH FROM planned_at) as pts, "
                    "duration_min, equipment "
                    "FROM workout_sessions "
                    "WHERE user_id=%s AND status='planned' "
                    "AND EXTRACT(EPOCH FROM planned_at) >= %s "
                    "AND EXTRACT(EPOCH FROM planned_at) < %s",
                    (user_id, from_ts, to_ts),
                )
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "session_type": r[1],
                        "planned_at_ts": float(r[2] or 0),
                        "duration_min": r[3],
                        "equipment": list(r[4] or []),
                        "user_id": user_id,
                    }
                    for r in rows
                ]
        except Exception as exc:
            log.warning("[FitnessService] _get_planned_sessions: %s", exc)
            return []

    def register_schedule_callbacks(self) -> None:
        """Registra callback en ScheduleService para reaccionar a eventos nuevos."""
        try:
            from modules.schedule.service import register_event_callback

            async def on_event_created(event) -> None:
                profile = self.get_profile(event.user_id)
                protected = set(profile.get("protected_categories", []))
                if event.category in protected:
                    await self.check_and_resolve_conflicts(event.user_id)

            register_event_callback("created", on_event_created)
            log.info("[FitnessService] callback 'created' registrado")
        except Exception as exc:
            log.warning("[FitnessService] register_schedule_callbacks: %s", exc)

    def on_load(self) -> None:
        """Hook de carga — registrar callbacks."""
        self.register_schedule_callbacks()


# ── Singleton ────────────────────────────────────────────────────────

_singleton: Optional[FitnessService] = None
_singleton_lock = threading.Lock()


def get_fitness_service() -> FitnessService:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = FitnessService()
    return _singleton
