"""DailyBriefingSkill — briefing matutino: agenda + Canvas + clima.

Responde a queries como "que tengo hoy", "dame el briefing", "/briefing".
Tambien permite cambiar la hora del briefing automatico por voz.
"""
import asyncio
import re
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

_DAY_NAMES_ES = [
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
]

import httpx

from core.request_context import RequestContext
from skills.base import BaseSkill

_WEATHER_URL = "https://wttr.in/?format=3"

_TRIGGERS_EXACT = ["/briefing", "/resumen", "briefing", "resumen"]
_TRIGGERS_CONTAINS = [
    "que tengo hoy", "resumen del dia", "que hay hoy",
    "dame el briefing", "que tengo para hoy",
]

_BRIEFING_HOUR_RE = re.compile(
    r'(?:cambi[ae]|pon[eé]|mueve)\s+(?:el\s+)?(?:briefing|resumen)'
    r'\s+(?:a\s+las?\s+)?(\d{1,2})',
    re.IGNORECASE,
)


class DailyBriefingSkill(BaseSkill):

    def __init__(self, metadata=None, skill_dir: Optional[Path] = None):
        super().__init__(metadata=metadata, skill_dir=skill_dir)
        self._notifier = None

    def set_notifier(self, notifier):
        self._notifier = notifier

    def matches_command(self, user_input: str) -> bool:
        lower = user_input.lower().strip()
        if any(lower.startswith(t) for t in _TRIGGERS_EXACT):
            return True
        if any(t in lower for t in _TRIGGERS_CONTAINS):
            return True
        if _BRIEFING_HOUR_RE.search(lower):
            return True
        return False

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        lower = user_input.lower().strip()

        m = _BRIEFING_HOUR_RE.search(lower)
        if m:
            return self._handle_change_hour(int(m.group(1)))

        return await self.generate_briefing()

    def _handle_change_hour(self, hour: int) -> str:
        if not (0 <= hour <= 23):
            return f"La hora debe estar entre 0 y 23. '{hour}' no es valido."
        if self._notifier:
            self._notifier.set_briefing_hour(hour)
        return f"Briefing matutino programado a las {hour}:00."

    async def generate_briefing(self) -> str:
        now_ts = time.time()
        day_end = now_ts + 86400

        events, assignments, weather = await asyncio.gather(
            self._get_schedule_events(now_ts, day_end),
            self._get_canvas_assignments(),
            self._get_weather(),
            return_exceptions=True,
        )

        events = events if not isinstance(events, Exception) else []
        assignments = assignments if not isinstance(assignments, Exception) else []
        weather = weather if not isinstance(weather, Exception) else ""

        data_str = self._format_briefing_data(events, assignments, weather)
        prompt = (
            "Sos Zephyro, un asistente personal. Genera un briefing matutino breve "
            "y natural en espanol para Bruno. Maximo 4 oraciones. Sin markdown. Sin emojis.\n\n"
            + data_str
        )

        if self.llm_manager:
            try:
                response = await asyncio.to_thread(
                    lambda: self.llm_manager.generate(
                        prompt, force_local=True, max_tokens=200,
                    )
                )
                return response.strip() if response else "No pude generar el briefing."
            except Exception:
                pass

        return self._format_briefing_fallback(events, assignments, weather)

    async def _get_schedule_events(self, from_ts: float, to_ts: float) -> list:
        try:
            from modules.schedule.service import get_schedule_service
            svc = get_schedule_service()
            return svc.get_events_in_range("admin", from_ts, to_ts)
        except Exception:
            return []

    async def _get_canvas_assignments(self) -> list:
        try:
            from modules.canvas.client import CanvasClient
            canvas = CanvasClient()
            if not canvas.is_configured():
                return []
            return await canvas.get_upcoming_assignments(days=1)
        except Exception:
            return []

    async def _get_weather(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(_WEATHER_URL)
                return r.text.strip() if r.status_code == 200 else ""
        except Exception:
            return ""

    def _format_briefing_data(
        self, events: list, assignments: list, weather: str
    ) -> str:
        lines = []

        today_name = _DAY_NAMES_ES[datetime.now().weekday()]
        lines.append(f"Hoy es {today_name}.")

        if weather:
            lines.append(f"Clima: {weather}")

        if events:
            lines.append("Eventos de hoy:")
            for ev in events[:10]:
                start_dt = datetime.fromtimestamp(ev.start_ts_utc)
                lines.append(
                    f"  - {start_dt.strftime('%H:%M')} {ev.title}"
                    f" ({ev.category or 'otro'})"
                )
        else:
            lines.append("No hay eventos en la agenda hoy.")

        if assignments:
            lines.append("Tareas de Canvas pendientes:")
            for a in assignments[:8]:
                title = a.get("title", "Sin titulo")
                course = a.get("course", "")
                due = a.get("due_at")
                due_str = ""
                if due:
                    if isinstance(due, str):
                        due_str = due[:16]
                    else:
                        due_str = due.strftime("%H:%M")
                label = f"{title} ({course})" if course else title
                lines.append(f"  - {label}" + (f" — {due_str}" if due_str else ""))
        else:
            lines.append("No hay tareas pendientes en Canvas.")

        return "\n".join(lines)

    def _format_briefing_fallback(
        self, events: list, assignments: list, weather: str
    ) -> str:
        """Resumen basico sin LLM (fallback)."""
        parts = []
        today_name = _DAY_NAMES_ES[datetime.now().weekday()]
        parts.append(f"Buen dia Bruno. Hoy es {today_name}.")

        if weather:
            parts.append(f"Clima: {weather}.")

        event_count = len(events)
        if event_count:
            first = events[0]
            start_dt = datetime.fromtimestamp(first.start_ts_utc)
            parts.append(
                f"Tenes {event_count} evento{'s' if event_count > 1 else ''} hoy. "
                f"El primero: {first.title} a las {start_dt.strftime('%H:%M')}."
            )
        else:
            parts.append("No tenes eventos en la agenda hoy.")

        if assignments:
            parts.append(f"Tenes {len(assignments)} tarea{'s' if len(assignments) > 1 else ''} pendiente{'s' if len(assignments) > 1 else ''} en Canvas.")
        else:
            parts.append("No hay tareas pendientes en Canvas.")

        return " ".join(parts)
