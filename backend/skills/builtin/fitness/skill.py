"""Fitness skill — wrapper sobre FitnessService.

Sigue el patrón exacto de schedule/skill.py:
  - _get_service() lazy
  - set_llm_manager heredado de BaseSkill
  - handle() con regex matching en español tuteo
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Timezone Lima
try:
    from zoneinfo import ZoneInfo
    _LIMA = ZoneInfo("America/Lima")
except ImportError:
    import pytz
    _LIMA = pytz.timezone("America/Lima")

# ── Día → número (0=lun .. 6=dom) ──────────────────────────────────
_WEEKDAYS_ES = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}

# ── Regex de comandos ───────────────────────────────────────────────

# Generar plan
_GENERATE_RE = re.compile(
    r"\b(?:genera(?:me)?|crea(?:me)?|haz(?:me)?|quiero|dame|necesito)\b"
    r"(?:\s+\w+){0,4}\s+(?:mi\s+)?(?:rutina|plan|programa|entreno|entrenamiento|ejercicios?)\b"
    r"|\b(?:/fitness|hazme\s+un\s+plan)\b",
    re.IGNORECASE,
)

# Días preferidos: "entreno los martes y jueves"
_PREFERRED_DAYS_RE = re.compile(
    r"\b(?:entreno|prefiero\s+(?:entrenar|ir)|quiero\s+entrenar)\b"
    r"(?:\s+(?:los?|el|la))?"
    r"(?:\s*(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|y|,))+",
    re.IGNORECASE,
)

# Horario preferido
_PREFERRED_TIME_RE = re.compile(
    r"\bprefiero\s+entrenar\b.{0,30}(?P<window>ma[ñn]ana|tarde|noche|cualquier\s+hora)",
    re.IGNORECASE,
)

# Cambiar contexto
_UPGRADE_RE = re.compile(
    r"\b(?:cambiar?\s+a|pasarme?\s+a|voy\s+a|modo)\s+"
    r"(?:la\s+|el\s+)?"
    r"(?P<context>gym|gimnasio|muay\s*thai|nataci[oó]n|natacion|piscina|casa|home)\b",
    re.IGNORECASE,
)

# Reprogramar sesión
_RESCHEDULE_RE = re.compile(
    r"\b(?:reprogram[aá]|mueve|mov[eé]|pasa)\s+(?:mi\s+)?(?:entreno|sesión|sesion|entrenamiento)\b"
    r"|\breprograma\s+mi\s+entreno\b",
    re.IGNORECASE,
)

# Log sesión
_LOG_SESSION_RE = re.compile(
    r"\b(?:hice|hic[eé]|complet[eé]|termin[eé]|acab[eé])\s+(?:mi\s+|la\s+|el\s+|una\s+)?(?:entreno|sesión|sesion|entrenamiento|rutina)\b"
    r"|\bya\s+(?:entren[eé]|hice\s+(?:mi\s+)?(?:entreno|rutina))\b",
    re.IGNORECASE,
)

# Racha
_STREAK_RE = re.compile(
    r"\b(?:mi\s+racha|/racha|cu[aá]ntos?\s+d[ií]as\s+(?:llevo|tengo)|racha\s+de\s+entreno)\b",
    re.IGNORECASE,
)

# Resumen semanal
_WEEKLY_RE = re.compile(
    r"\b(?:resumen\s+(?:semanal|de\s+(?:la\s+|mi\s+)?semana)|c[oó]mo\s+(?:me\s+fue|voy)|estad[ií]sticas?\s+de\s+(?:la\s+)?semana)\b",
    re.IGNORECASE,
)

# Alternativas de slot
_ALTERNATIVES_RE = re.compile(
    r"\b(?:alternativas?|opciones?|otros?\s+horarios?|cu[aá]ndo\s+puedo\s+entrenar)\b",
    re.IGNORECASE,
)


def _extract_days(text: str) -> list[int]:
    """Extrae números de día de semana (0-6) del texto."""
    text_lower = text.lower()
    days = []
    for name, num in _WEEKDAYS_ES.items():
        if re.search(rf"\b{name}\b", text_lower):
            if num not in days:
                days.append(num)
    return sorted(days)


def _context_from_text(text: str) -> Optional[str]:
    """Extrae contexto de plan del texto."""
    text_lower = text.lower()
    if re.search(r"\bgymnasio?\b|\bgym\b", text_lower):
        return "gym"
    if re.search(r"\bmuay\s*thai\b", text_lower):
        return "muay_thai"
    if re.search(r"\bnataci[oó]n\b|\bnadar\b|\bpiscina\b", text_lower):
        return "swimming"
    if re.search(r"\bcasa\b|\bhome\b", text_lower):
        return "home"
    return None


class FitnessSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._service = None

    def _get_service(self):
        if self._service is None:
            from modules.fitness.service import get_fitness_service
            self._service = get_fitness_service()
            # Inyectar LLM y vision si están disponibles
            if self.llm_manager:
                self._service.set_llm_manager(self.llm_manager)
        return self._service

    # ── Lifecycle ───────────────────────────────────────────────────

    async def on_load(self) -> None:
        svc = self._get_service()
        svc.on_load()

    # ── Matching ────────────────────────────────────────────────────

    def matches_command(self, user_input: str, ctx=None) -> bool:
        lower = user_input.lower().strip()
        for trig in self.metadata.triggers:
            t = trig.lower()
            if lower == t or lower.startswith(t):
                return True
        return bool(
            _GENERATE_RE.search(lower)
            or _PREFERRED_DAYS_RE.search(lower)
            or _PREFERRED_TIME_RE.search(lower)
            or _UPGRADE_RE.search(lower)
            or _RESCHEDULE_RE.search(lower)
            or _LOG_SESSION_RE.search(lower)
            or _STREAK_RE.search(lower)
            or _WEEKLY_RE.search(lower)
        )

    # ── Handle ──────────────────────────────────────────────────────

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        user_id = (ctx.user_role if ctx and hasattr(ctx, "user_role") else None) or "admin"
        lower = user_input.lower().strip()
        svc = self._get_service()

        # Inyectar LLM actualizado
        if self.llm_manager and svc._llm_manager is None:
            svc.set_llm_manager(self.llm_manager)

        # ── Generar plan ────────────────────────────────────────────
        if _GENERATE_RE.search(lower) or lower in ("/fitness",):
            context = _context_from_text(lower)
            try:
                plan = await svc.generate_home_plan(user_id=user_id, context=context)
                result = svc.materialize_plan_to_agenda(plan, user_id=user_id, weeks=4)
                raw = plan.get("raw", {})
                freq = raw.get("weekly_frequency", "?")
                rationale = raw.get("rationale", "")
                scheduled = result.get("scheduled", 0)
                unscheduled = result.get("unscheduled", 0)
                resp = (
                    f"Plan listo! {freq} sesiones/semana.\n"
                    f"{rationale}\n\n"
                    f"Agendé {scheduled} sesiones en las próximas 4 semanas."
                )
                if unscheduled:
                    resp += f" {unscheduled} sesión(es) sin slot — revisá tu agenda."
                return resp
            except Exception as exc:
                logger.warning("[FitnessSkill] generate_home_plan error: %s", exc)
                return "No pude generar el plan ahora. Revisá que haya conexión y volvé a intentarlo."

        # ── Días preferidos ─────────────────────────────────────────
        if _PREFERRED_DAYS_RE.search(lower):
            days = _extract_days(lower)
            if days:
                day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
                svc.set_preferred_days(user_id, days)
                names = [day_names[d] for d in days]
                return f"Preferencia guardada: entrenas {', '.join(names)}. Al regenerar el plan usará esos días."
            return "No entendí qué días querés. Decime por ejemplo: 'entreno los martes y jueves'."

        # ── Horario preferido ───────────────────────────────────────
        m = _PREFERRED_TIME_RE.search(lower)
        if m:
            window_raw = m.group("window").lower()
            mapping = {
                "mañana": ("morning", 7),
                "manana": ("morning", 7),
                "tarde": ("afternoon", 14),
                "noche": ("evening", 19),
            }
            for k, (tw, hour) in mapping.items():
                if k in window_raw:
                    svc.update_profile(user_id, preferred_time_window=tw, preferred_start_hour=hour)
                    return f"Preferencia guardada: entrenas de {window_raw} (alrededor de las {hour}h Lima)."
            svc.update_profile(user_id, preferred_time_window="any")
            return "Preferencia guardada: horario flexible."

        # ── Cambiar contexto ────────────────────────────────────────
        m = _UPGRADE_RE.search(lower)
        if m:
            ctx_raw = m.group("context").strip().lower().replace(" ", "_")
            ctx_map = {
                "gym": "gym", "gimnasio": "gym",
                "muay_thai": "muay_thai", "muaythai": "muay_thai",
                "natación": "swimming", "natacion": "swimming",
                "piscina": "swimming",
                "casa": "home", "home": "home",
            }
            new_ctx = ctx_map.get(ctx_raw, "home")
            try:
                plan = await svc.upgrade_plan(user_id, new_ctx)
                raw = plan.get("raw", {})
                freq = raw.get("weekly_frequency", "?")
                return f"Cambié el plan a contexto '{new_ctx}'. {freq} sesiones/semana. Regenerá el plan para actualizarlo en tu agenda."
            except Exception as exc:
                logger.warning("[FitnessSkill] upgrade_plan error: %s", exc)
                return f"No pude cambiar el plan: {exc}"

        # ── Reprogramar sesión ──────────────────────────────────────
        if _RESCHEDULE_RE.search(lower):
            sessions = svc._get_planned_sessions(
                user_id,
                from_ts=datetime.now(timezone.utc).timestamp(),
                to_ts=datetime.now(timezone.utc).timestamp() + 86400 * 2,
            )
            if not sessions:
                return "No tenés sesiones planeadas en las próximas 48 horas para reprogramar."
            session = sessions[0]
            alternatives = svc.suggest_alternative_slots(session["id"], n=3)
            if not alternatives:
                return "No encontré slots alternativos esta semana. Revisá tu agenda."
            opts = []
            for i, alt in enumerate(alternatives, 1):
                alt_dt = datetime.fromtimestamp(alt["start"], tz=_LIMA)
                opts.append(f"{i}) {alt_dt.strftime('%A %d/%m %H:%M')}")
            return (
                f"Tu próxima sesión es '{session['session_type']}'. "
                f"Slots alternativos disponibles:\n" + "\n".join(opts) +
                "\nRespondeme el número para confirmar el cambio."
            )

        # ── Log sesión ──────────────────────────────────────────────
        if _LOG_SESSION_RE.search(lower):
            sessions = svc._get_planned_sessions(
                user_id,
                from_ts=datetime.now(timezone.utc).timestamp() - 86400,
                to_ts=datetime.now(timezone.utc).timestamp() + 3600,
            )
            if not sessions:
                return "No encontré sesiones planeadas para hoy o ayer. Si fue hace más tiempo, decime el ID de sesión."
            session = sessions[-1]  # La más reciente
            result = svc.log_session(
                session["id"],
                notes=user_input,
                verification_method="self_report",
            )
            streak = result.get("streak", {})
            current = streak.get("current", 0)
            streak_msg = f"Racha actual: {current} día(s)." if current else "Seguí así para empezar una racha!"
            return f"Perfecto, registré tu entrenamiento de '{session['session_type']}'. {streak_msg}"

        # ── Racha ────────────────────────────────────────────────────
        if _STREAK_RE.search(lower) or lower in ("/racha",):
            streak = svc.get_streak(user_id)
            current = streak.get("current", 0)
            longest = streak.get("longest", 0)
            last = streak.get("last_date", "nunca")
            if current == 0:
                return f"No tenés racha activa. Última sesión: {last}. Tu récord es {longest} días."
            return f"Racha actual: {current} día(s). Récord: {longest} días. Último entreno: {last}."

        # ── Resumen semanal ──────────────────────────────────────────
        if _WEEKLY_RE.search(lower):
            summary = svc.get_weekly_summary(user_id)
            completed = summary.get("completed", 0)
            planned = summary.get("planned", 0)
            missed = summary.get("missed", 0)
            streak = summary.get("streak", {})
            current_streak = streak.get("current", 0) if isinstance(streak, dict) else 0
            week_start = summary.get("week_start", "esta semana")
            return (
                f"Semana del {week_start}:\n"
                f"  Completadas: {completed}\n"
                f"  Pendientes: {planned}\n"
                f"  Perdidas: {missed}\n"
                f"  Racha actual: {current_streak} día(s)"
            )

        # ── Alternativas ─────────────────────────────────────────────
        if _ALTERNATIVES_RE.search(lower):
            sessions = svc._get_planned_sessions(
                user_id,
                from_ts=datetime.now(timezone.utc).timestamp(),
                to_ts=datetime.now(timezone.utc).timestamp() + 86400 * 7,
            )
            if not sessions:
                return "No tenés sesiones planeadas esta semana."
            session = sessions[0]
            alternatives = svc.suggest_alternative_slots(session["id"], n=3)
            if not alternatives:
                return "No encontré slots libres esta semana."
            opts = []
            for i, alt in enumerate(alternatives, 1):
                alt_dt = datetime.fromtimestamp(alt["start"], tz=_LIMA)
                opts.append(f"{i}) {alt_dt.strftime('%A %d/%m %H:%M')}")
            return "Slots disponibles:\n" + "\n".join(opts)

        return None
