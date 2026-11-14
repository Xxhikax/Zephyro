"""ProactiveNotifier — push de recordatorios y eventos proximos sin que el usuario pregunte.

Loop de 60s que consulta:
- Eventos de agenda proximos (ScheduleService)
- Tareas proximas de Canvas (CanvasClient) — lógica de tiering inteligente:
    * Faltantes: una sola notificación (si ya no se puede entregar) o pregunta
      interactiva [SÍ/NO] (si aún se puede). Si [SÍ] → recuerda a las 8am y 8pm.
    * Pendientes: notifica a las 8am y 8pm. Si hay 4+ → recomendación grupal
      con [OK] (cycle 4h) o [IGNORAR] (silencio hasta el día siguiente).
- Recordatorios proximos (ReminderSystem)
- Briefing matutino diario (DailyBriefingSkill, solo via WebSocket si hay conectados)
- Notifica via WebSocket + canales (Telegram/WhatsApp si estan activos)

## Entrega: NotificationCenter (Fase 1, ADR-011)

Los avisos de agenda, recordatorios y briefing ya NO llaman a los sinks a mano:
construyen un ``NotificationEvent`` y lo emiten por ``core/notification_center``,
que aplica las reglas de ``core/notification_rules`` (severidad, condición, horas
de silencio, cooldown con dedup persistente) y recién ahí entrega.

El payload se sigue armando igual y viaja con ``subtype``/``type`` explícitos +
``telegram_text``, así el contrato con el frontend y el texto de Telegram quedan
idénticos a antes de la migración.

**Canvas sigue en el camino viejo a propósito**: su state machine
(``modules/canvas/notification_state``) ya hace su propio dedup y cooldown por
item con botones interactivos. Meterla abajo de las reglas ahora arriesga doble
supresión (silenciar una entrega), sin ganancia. Migra en una slice posterior.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from core.notification_rules import (
    EVENT_BRIEFING_READY,
    EVENT_EXPENSES_BUDGET_THRESHOLD,
    EVENT_REMINDER_DUE,
    EVENT_SCHEDULE_MISSED,
    EVENT_SCHEDULE_UPCOMING,
    NotificationEvent,
)

# Ventanas de notificación fijas para entregas pendientes (hora local)
_NOTIFY_HOURS = frozenset({8, 20})   # 8am y 8pm
_NOTIFY_WINDOW_MIN = 5               # minutos de gracia tras la hora exacta
_PENDING_THRESHOLD = 4               # 4+ pendientes → recomendación proactiva
_OK_INTERVAL_H = 4                   # horas del ciclo OK

log = logging.getLogger("zephyro.notifier")

CATEGORY_ICONS = {
    "university": "📚", "uni_exam": "📝", "uni_assignment": "📋",
    "work": "💼", "personal": "👤", "health": "🏥",
    "meeting": "🤝", "fitness": "🥊", "exercise": "🏃", "other": "📌",
}
CATEGORY_LABELS = {
    "university": "Universidad", "uni_exam": "Examen", "uni_assignment": "Tarea",
    "work": "Trabajo", "personal": "Personal", "health": "Salud",
    "meeting": "Reunión", "fitness": "Entreno", "exercise": "Ejercicio", "other": "Otro",
}

CANVAS_CHECK_INTERVAL = 1800  # segundos entre chequeos de Canvas (30 min)
BUDGET_CHECK_INTERVAL = 3600  # segundos entre chequeos de presupuesto (1 h)


# Perfiles de recordatorio por evento. El campo `event.reminder_profile` (None == 'default')
# decide qué stages se disparan.
#   pre_minutes  : lista de minutos antes del start_ts en que se notifica
#   at_start     : si True, push adicional al momento exacto del start_ts
#   missed_minutes: si != None, marca el evento como 'missed' a start_ts + N min sin confirmar
REMINDER_PROFILES: dict[str, dict] = {
    "default": {"pre_minutes": [10], "at_start": False, "missed_minutes": None},
    "fitness": {"pre_minutes": [60, 15], "at_start": True, "missed_minutes": 30},
}

# Mensajes contextuales por stage (en español tuteo)
_STAGE_TEMPLATES = {
    "fitness": {
        "pre_60": "🥊 En 1h tu entreno: <b>{title}</b>. Hidrátate y prepara el setup.",
        "pre_15": "🥊 En 15 min: <b>{title}</b>. Última oportunidad para cambiar de chip.",
        "at_start": "🥊 <b>AHORA</b>: {title}. ¡Vamos!",
        "missed": "⚠️ No marcaste tu entreno <b>{title}</b>. ¿Lo hiciste? Respondé 'sí' o 'no'.",
    },
    "default": {
        "pre_default": "⏰ En {mins} min: <b>{title}</b>",
        "at_start": "🔴 <b>AHORA</b>: {title}",
        "missed": "⚠️ Evento perdido: {title}",
    },
}


class ProactiveNotifier:
    def __init__(
        self,
        schedule_service,
        reminder_system,
        channel_manager,
        ws_registry,
        user_id: str = "admin",
        notification_center=None,
    ):
        self.schedule_service = schedule_service
        self.reminder_system = reminder_system
        self.channel_manager = channel_manager
        self.ws_registry = ws_registry
        self.user_id = user_id

        self.LEAD_MINUTES = int(os.getenv("REMINDER_LEAD_MINUTES", "10"))
        self._notified: dict[str, float] = {}
        self._notify_ttl: float = 3600.0

        self.briefing_skill = None
        self._briefing_hour = int(os.getenv("BRIEFING_HOUR", "8"))
        self._briefing_hour_override: Optional[int] = None
        self._last_briefing_date: Optional[str] = None

        self.canvas_client = None
        self._last_canvas_check: float = 0.0
        self._canvas_enabled = bool(os.getenv("CANVAS_API_TOKEN"))
        self._last_budget_check: float = 0.0

        # NotificationCenter: se configura acá porque este __init__ ya recibe los
        # dos sinks desde web_app, así ningún productor nuevo (skills, services)
        # necesita que web_app le pase nada. Si la DB de reglas no se puede abrir,
        # queda None y los avisos siguen por el camino directo de siempre: una
        # notificación perdida es peor que un reinicio sin reglas.
        # `notification_center` se inyecta en tests para no tocar la DB real de
        # reglas; en producción se usa el singleton.
        self.notifications = None
        try:
            from core.notification_center import get_notification_center
            center = notification_center or get_notification_center()
            center.configure(ws_registry=ws_registry, channel_manager=channel_manager)
            center.subscribe_to_bus()
            self.notifications = center
            log.info("[notifier] NotificationCenter activo")
        except Exception as exc:
            log.warning(
                "[notifier] NotificationCenter no disponible (%s) — "
                "entrega directa sin reglas", exc,
            )

    @property
    def briefing_hour(self) -> int:
        return self._briefing_hour_override if self._briefing_hour_override is not None else self._briefing_hour

    def set_briefing_hour(self, hour: int):
        self._briefing_hour_override = hour
        log.info(f"[notifier] Hora de briefing cambiada a las {hour}:00")

    async def run_loop(self):
        while True:
            try:
                await asyncio.sleep(60)
                await self._tick()
            except asyncio.CancelledError:
                log.info("[notifier] Loop cancelado.")
                break
            except Exception as exc:
                log.warning(f"[notifier] tick error: {exc}")

    async def _tick(self):
        now = time.time()
        window_end = now + (self.LEAD_MINUTES * 60)

        await self._check_schedule_events(now, window_end)
        await self._check_canvas_events(now)
        await self._check_reminders(now, window_end)
        await self._check_briefing(now)
        await self._check_budgets(now)
        await self._check_ci(now)

        cutoff = now - self._notify_ttl
        if self._notified:
            self._notified = {k: v for k, v in self._notified.items() if v > cutoff}

    async def _check_briefing(self, now: float):
        if self.briefing_skill is None:
            return

        dt = datetime.fromtimestamp(now)
        if dt.hour != self.briefing_hour:
            return

        today_str = dt.strftime("%Y-%m-%d")
        if self._last_briefing_date == today_str:
            return

        self._last_briefing_date = today_str
        try:
            text = await self.briefing_skill.generate_briefing()
            await self._emit(NotificationEvent(
                event_type=EVENT_BRIEFING_READY,
                title="Briefing",
                message=text,
                severity="info",
                dedup_key=f"briefing:{today_str}",
                payload={
                    # El frontend espera {type: briefing, text: ...}, no un reminder.
                    "type": "briefing",
                    "text": text,
                    "telegram_text": text,
                    "broadcast_all": True,
                },
            ))
            log.info("[notifier] Briefing enviado")
        except Exception as exc:
            log.warning(f"[notifier] Error generando briefing: {exc}")

    async def _check_ci(self, now: float):
        """Corridas de CI fallidas (Fase 1, productor nuevo).

        Opt-in con ``ZEPHYRO_CI_WATCH=1``: si no está seteado, el watcher se
        desactiva solo y esto no cuesta nada.
        """
        try:
            from modules.ci.watcher import get_ci_watcher
            events = await get_ci_watcher().poll(now)
        except Exception as exc:
            log.warning(f"[notifier] Error consultando CI: {exc}")
            return
        for event in events:
            await self._emit(event)

    async def _check_budgets(self, now: float):
        """Presupuestos del mes al límite o excedidos (Fase 1, productor nuevo).

        El aviso inline al registrar un gasto responde a lo que Bruno acaba de
        hacer; esto es lo proactivo: en la ventana de 8am/8pm avisa que el mes
        viene torcido aunque no haya preguntado. El cooldown de 12h de la regla
        y el dedup_key por categoría+nivel+mes evitan el spam.
        """
        if now - self._last_budget_check < BUDGET_CHECK_INTERVAL:
            return
        self._last_budget_check = now
        if not self._in_notify_window(datetime.fromtimestamp(now)):
            return

        try:
            from modules.expenses.service import (
                CATEGORY_LABELS, TOTAL_BUDGET_KEY, format_amount, get_expense_service,
            )
            statuses = get_expense_service().budget_status(self.user_id)
        except Exception as exc:
            log.warning(f"[notifier] Error consultando presupuestos: {exc}")
            return

        month = datetime.fromtimestamp(now).strftime("%Y-%m")
        for st in statuses:
            if st["level"] == "ok":
                continue
            scope = (
                "total" if st["category"] == TOTAL_BUDGET_KEY
                else CATEGORY_LABELS.get(st["category"], st["category"])
            )
            spent = format_amount(st["spent"], st["currency"])
            limit = format_amount(st["limit"], st["currency"])
            if st["level"] == "over":
                title = f"Presupuesto {scope} excedido"
                text = (
                    f"💸 <b>Presupuesto {scope} excedido</b>\n\n"
                    f"Gastaste {spent} de {limit} ({st['pct']:.0f}%) este mes."
                )
            else:
                title = f"Presupuesto {scope} al límite"
                text = (
                    f"⚠️ <b>Presupuesto {scope} al {st['pct']:.0f}%</b>\n\n"
                    f"{spent} de {limit}. Te quedan "
                    f"{format_amount(st['remaining'], st['currency'])}."
                )

            await self._emit(NotificationEvent(
                event_type=EVENT_EXPENSES_BUDGET_THRESHOLD,
                title=title,
                message=f"{spent} de {limit}",
                severity="warn",
                dedup_key=f"budget:{st['category']}:{st['level']}:{month}",
                payload={
                    "subtype": "expenses_budget",
                    "category": st["category"],
                    "level": st["level"],
                    "pct": round(st["pct"], 1),
                    "spent": st["spent"],
                    "limit": st["limit"],
                    "currency": st["currency"],
                    "icon": "💸" if st["level"] == "over" else "⚠️",
                    "category_label": f"Presupuesto {scope}",
                    "action": "open_expenses",
                    "telegram_text": text,
                },
            ))

    async def _check_schedule_events(self, now: float, window_end: float):
        # Ventana ampliada al mayor pre_minutes de todos los profiles para no perder T-60 de fitness.
        max_pre = 0
        for prof in REMINDER_PROFILES.values():
            for m in prof.get("pre_minutes", []):
                if m > max_pre:
                    max_pre = m
        widest_window = now + max(self.LEAD_MINUTES, max_pre) * 60

        try:
            upcoming = self.schedule_service.get_events_in_range(
                self.user_id, now, widest_window
            )
        except Exception as exc:
            log.warning(f"[notifier] Error consultando eventos próximos: {exc}")
            return

        # También buscamos eventos cuyo start_ts ya pasó pero pueden necesitar at_start o missed
        try:
            recent = self.schedule_service.get_events_in_range(
                self.user_id, now - 3600, now
            )
        except Exception:
            recent = []

        seen_ids = set()
        for ev in upcoming + recent:
            if ev.id in seen_ids:
                continue
            seen_ids.add(ev.id)
            await self._dispatch_stages(ev, now)

    async def _dispatch_stages(self, ev, now: float):
        profile_name = (getattr(ev, "reminder_profile", None) or "default")
        profile = REMINDER_PROFILES.get(profile_name, REMINDER_PROFILES["default"])
        start = ev.start_ts_utc

        # Stages pre_minutes: notificar dentro de ventana de 60s post-disparo
        for m in profile.get("pre_minutes", []):
            stage_ts = start - m * 60
            stage_key = f"event_{ev.id}_pre_{m}"
            if stage_key in self._notified:
                continue
            if 0 <= (now - stage_ts) < 60:
                await self._notify_stage(ev, "pre", m, profile_name)
                self._notified[stage_key] = now

        # Stage at_start
        if profile.get("at_start"):
            stage_key = f"event_{ev.id}_at_start"
            if stage_key not in self._notified and 0 <= (now - start) < 60:
                await self._notify_stage(ev, "at_start", 0, profile_name)
                self._notified[stage_key] = now

        # Stage missed
        missed_min = profile.get("missed_minutes")
        if missed_min is not None:
            stage_key = f"event_{ev.id}_missed"
            missed_ts = start + missed_min * 60
            if stage_key not in self._notified and now >= missed_ts:
                # Verificar status actual antes de marcar
                fresh = self.schedule_service.get_event_by_id(ev.id)
                if fresh and fresh.status == "pending":
                    try:
                        self.schedule_service.mark_missed(ev.id)
                    except Exception as exc:
                        log.warning(f"[notifier] mark_missed {ev.id} falló: {exc}")
                    await self._notify_stage(ev, "missed", missed_min, profile_name)
                self._notified[stage_key] = now

    async def _notify_stage(self, ev, kind: str, mins: int, profile_name: str):
        """Envía push WS + Telegram para un stage específico."""
        templates = _STAGE_TEMPLATES.get(profile_name, _STAGE_TEMPLATES["default"])

        if kind == "pre":
            key = f"pre_{mins}" if f"pre_{mins}" in templates else "pre_default"
            text = templates[key].format(title=ev.title, mins=mins)
            subtype = f"pre_{mins}"
        elif kind == "at_start":
            text = templates.get("at_start", "🔴 AHORA: {title}").format(title=ev.title)
            subtype = "at_start"
        elif kind == "missed":
            text = templates.get("missed", "⚠️ {title}").format(title=ev.title)
            subtype = "missed"
        else:
            text = f"{ev.title}"
            subtype = kind

        icon = CATEGORY_ICONS.get(ev.category, "📅")
        label = CATEGORY_LABELS.get(ev.category, ev.category or "Evento")
        await self._emit(NotificationEvent(
            event_type=EVENT_SCHEDULE_MISSED if kind == "missed" else EVENT_SCHEDULE_UPCOMING,
            title=ev.title,
            message=text,
            severity="warn" if kind == "missed" else "info",
            dedup_key=f"schedule:{ev.id}:{subtype}",
            payload={
                # subtype explícito: el frontend rutea por él (src/chat.js), así
                # que mantiene el valor viejo y no el nombre del evento nuevo.
                "subtype": f"schedule_{subtype}",
                "event_id": ev.id,
                "category": ev.category or "other",
                "category_label": label,
                "icon": icon,
                "profile": profile_name,
                "stage": subtype,
                "start_ts": ev.start_ts_utc,
                "action": "open_agenda",
                "telegram_text": text,
            },
        ))

    async def _check_canvas_events(self, now: float):
        """Entry point: fan-out to missing + pending checks."""
        if not self._canvas_enabled or not self.canvas_client:
            return
        if now - self._last_canvas_check < CANVAS_CHECK_INTERVAL:
            return
        self._last_canvas_check = now
        try:
            missing, upcoming = await asyncio.gather(
                self.canvas_client.get_missing_submissions(),
                self.canvas_client.get_upcoming_assignments(days=7),
                return_exceptions=True,
            )
            if not isinstance(missing, Exception):
                await self._check_canvas_missing(missing, now)
            else:
                log.warning(f"[notifier] Canvas missing error: {missing}")
            if not isinstance(upcoming, Exception):
                await self._check_canvas_pending(upcoming, now)
            else:
                log.warning(f"[notifier] Canvas pending error: {upcoming}")
        except Exception as exc:
            log.warning(f"[notifier] Error consultando Canvas: {exc}")

    # ── Canvas: faltantes ────────────────────────────────────────────

    async def _check_canvas_missing(self, missing: list, now: float):
        from modules.canvas.notification_state import get_canvas_notification_state
        ns = get_canvas_notification_state()

        for m in missing:
            item_id = str(m.get("id", ""))
            if not item_id:
                continue

            rec = ns.get(item_id, "missing")
            state = rec["state"] if rec else "new"
            next_at = rec["next_notify_at"] if rec else 0.0

            if state == "done":
                continue
            if state == "asked":
                continue  # waiting for user response

            title = m.get("title", "Tarea")
            course = m.get("course", "")
            display = f"{title} — {course}" if course else title
            due = m.get("due_at")
            due_str = (
                due.strftime("%d/%m %H:%M")
                if hasattr(due, "strftime") else str(due or "Sin fecha")
            )
            lock_at = m.get("lock_at")
            url = m.get("url", "")
            submittable = (
                lock_at is None
                or (hasattr(lock_at, "timestamp") and lock_at.timestamp() > now)
            )

            if state == "new":
                # Broadcast WebSocket regardless
                await self.ws_registry.broadcast({
                    "type": "reminder",
                    "subtype": "canvas_missing",
                    "event_id": item_id,
                    "title": display,
                    "message": f"Fecha límite: {due_str}",
                    "icon": "⚠️",
                    "category": "uni_assignment",
                    "category_label": "Entrega faltante",
                })
                if not submittable:
                    # Notify once via Telegram — no buttons
                    text = (
                        f"⚠️ <b>Entrega faltante (sin plazo)</b>\n\n"
                        f"<b>{display}</b>\n"
                        f"📅 Fecha límite: {due_str}\n\n"
                        f"Esta entrega ya no puede enviarse."
                    )
                    await self._telegram_send(text)
                    ns.set_state(item_id, "missing", "done")
                else:
                    # Ask user via interactive buttons
                    text = (
                        f"⚠️ <b>Entrega faltante</b>\n\n"
                        f"<b>{display}</b>\n"
                        f"📅 Fecha límite: {due_str}\n\n"
                        f"Esta entrega aún puede enviarse. ¿Planeas entregarla?"
                    )
                    keyboard = [
                        [
                            ("✅ Sí, la entregaré", f"canvas:missing:{item_id}:si"),
                            ("❌ No, ignorar",       f"canvas:missing:{item_id}:no"),
                        ]
                    ]
                    await self._telegram_send_interactive(text, keyboard)
                    ns.set_state(item_id, "missing", "asked")

            elif state == "will_submit":
                # 8am / 8pm reminder cycle
                if not self._in_notify_window(datetime.fromtimestamp(now)):
                    continue
                if next_at > 0 and now < next_at:
                    continue
                text = (
                    f"⏰ <b>Recordatorio de entrega</b>\n\n"
                    f"<b>{display}</b>\n"
                    f"📅 Fecha límite: {due_str}\n\n"
                    f"¿Ya la entregaste?"
                )
                keyboard = [
                    [
                        ("✅ Sí, ya entregué",   f"canvas:missing:{item_id}:entregue"),
                        ("🔔 Recuérdame luego", f"canvas:missing:{item_id}:recordar"),
                    ]
                ]
                await self._telegram_send_interactive(text, keyboard)
                # next reminder at next notify window (~12h away from opposite slot)
                next_notify = self._next_notify_ts(now)
                ns.set_state(item_id, "missing", "will_submit", next_notify)

    # ── Canvas: pendientes ───────────────────────────────────────────

    async def _check_canvas_pending(self, upcoming: list, now: float):
        if not upcoming:
            return

        from modules.canvas.notification_state import get_canvas_notification_state
        ns = get_canvas_notification_state()

        rec = ns.get("global", "pending")
        state = rec["state"] if rec else "new"
        next_at = rec["next_notify_at"] if rec else 0.0

        should_notify = False

        if state in ("new", None):
            should_notify = self._in_notify_window(datetime.fromtimestamp(now))
        elif state == "ok":
            should_notify = now >= next_at
        elif state == "ignored":
            if now >= next_at:
                # Reset to new after ignored period ends
                ns.set_state("global", "pending", "new")
                should_notify = self._in_notify_window(datetime.fromtimestamp(now))
            else:
                should_notify = False

        if not should_notify:
            return

        count = len(upcoming)
        # Sort by due date
        upcoming_sorted = sorted(
            upcoming,
            key=lambda a: a["due_at"] if hasattr(a.get("due_at"), "timestamp") else 0,
        )

        # WS broadcast
        await self.ws_registry.broadcast({
            "type": "reminder",
            "subtype": "canvas_pending_summary",
            "count": count,
            "items": [
                {
                    "title": a.get("title", ""),
                    "course": a.get("course", ""),
                    "due_at": a["due_at"].isoformat() if hasattr(a.get("due_at"), "isoformat") else "",
                }
                for a in upcoming_sorted[:5]
            ],
        })

        if count >= _PENDING_THRESHOLD:
            # Grouped recommendation with interactive buttons
            lines = [f"📋 <b>Tienes {count} entregas pendientes</b>\n"]
            for a in upcoming_sorted[:6]:
                t = a.get("title", "?")
                c = a.get("course", "")
                due = a.get("due_at")
                due_s = due.strftime("%d/%m %H:%M") if hasattr(due, "strftime") else "?"
                lines.append(f"  • <b>{t}</b> — {c} | {due_s}")
            if count > 6:
                lines.append(f"  … y {count - 6} más")
            lines.append(
                "\n💡 Para no acumularte, intenta avanzar con la más próxima primero."
            )
            text = "\n".join(lines)
            keyboard = [
                [
                    ("✅ OK, recuérdame en 4h", "canvas:pending:ok"),
                    ("🔕 Ignorar hasta mañana", "canvas:pending:ignorar"),
                ]
            ]
            await self._telegram_send_interactive(text, keyboard)
            # Set to ok with 4h window after sending grouped message
            ns.set_state("global", "pending", "ok", now + _OK_INTERVAL_H * 3600)
        else:
            # Simple info message — no buttons
            lines = [f"📋 <b>Entregas próximas ({count})</b>\n"]
            for a in upcoming_sorted:
                t = a.get("title", "?")
                c = a.get("course", "")
                due = a.get("due_at")
                due_s = due.strftime("%d/%m %H:%M") if hasattr(due, "strftime") else "?"
                lines.append(f"  • <b>{t}</b> — {c} | {due_s}")
            await self._telegram_send("\n".join(lines))
            # Reset for next notify window
            ns.set_state("global", "pending", "new", self._next_notify_ts(now))

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _in_notify_window(dt: datetime) -> bool:
        return dt.hour in _NOTIFY_HOURS and dt.minute < _NOTIFY_WINDOW_MIN

    @staticmethod
    def _next_notify_ts(now: float) -> float:
        """Timestamp of the next 8am or 8pm slot after `now`."""
        dt = datetime.fromtimestamp(now)
        candidates = []
        for h in sorted(_NOTIFY_HOURS):
            candidate = dt.replace(hour=h, minute=0, second=0, microsecond=0)
            if candidate.timestamp() > now:
                candidates.append(candidate.timestamp())
        if candidates:
            return min(candidates)
        # Both slots are past today → first slot tomorrow
        tomorrow = (dt + timedelta(days=1)).replace(
            hour=min(_NOTIFY_HOURS), minute=0, second=0, microsecond=0
        )
        return tomorrow.timestamp()

    async def _telegram_send_interactive(self, text: str, keyboard: list):
        """Send an interactive Telegram message with inline keyboard buttons.

        keyboard format: list of rows, each row is a list of (label, callback_data) tuples.
        """
        if not self.channel_manager:
            return
        tg = self.channel_manager.get_channel("telegram")
        if not tg or not hasattr(tg, "send_canvas_interactive"):
            # Fallback to plain text if channel doesn't support interactive
            await self._telegram_send(text)
            return
        for cid in tg.get_authorized_ids():
            raw_id = int(cid.replace("telegram:", ""))
            try:
                await tg.send_canvas_interactive(raw_id, text, keyboard)
            except Exception as exc:
                log.warning(f"[notifier] telegram interactive send failed: {exc}")

    async def _check_reminders(self, now: float, window_end: float):
        if self.reminder_system is None:
            return
        try:
            reminders = self.reminder_system.get_reminders_due_before(window_end)
        except Exception as exc:
            log.warning(f"[notifier] Error consultando recordatorios: {exc}")
            return

        for r in reminders:
            key = f"reminder_{r.id}"
            if key not in self._notified:
                await self._notify_reminder(r, now)
                self._notified[key] = now

    async def _notify_reminder(self, r, now: float):
        mins = max(0, int((r.due_at - now) / 60))
        time_str = "AHORA" if mins <= 0 else f"En {mins} min"
        tg_time = "🔴 <b>AHORA</b>" if mins <= 0 else f"⏰ En {mins} minutos"
        await self._emit(NotificationEvent(
            event_type=EVENT_REMINDER_DUE,
            title=r.message,
            message=time_str,
            severity="info",
            dedup_key=f"reminder:{r.id}",
            payload={
                "subtype": "reminder",
                "reminder_id": r.id,
                "mins_remaining": mins,
                "icon": "⏰",
                "category_label": "Recordatorio",
                "action": "open_reminders",
                "telegram_text": (
                    f"⏰ <b>Recordatorio</b>\n\n<b>{r.message}</b>\n{tg_time}"
                ),
            },
        ))

    # ── Telegram helpers ─────────────────────────────────────────────

    @staticmethod
    def _category_label(category: str) -> str:
        return CATEGORY_LABELS.get(category, category or "Otro")

    async def _telegram_send(self, text: str):
        if not self.channel_manager:
            return
        tg = self.channel_manager.get_channel("telegram")
        if not tg:
            return
        for cid in tg.get_authorized_ids():
            try:
                await tg.send(cid, text, parse_mode="HTML")
            except Exception:
                pass

    # ── Salida única ─────────────────────────────────────────────────

    async def _emit(self, event: NotificationEvent) -> None:
        """Emite por el NotificationCenter (reglas + dedup persistente).

        Si el center no está disponible, replica el camino directo de siempre:
        perder un aviso es peor que notificar sin reglas.
        """
        if self.notifications is not None:
            try:
                await self.notifications.emit(event)
                return
            except Exception as exc:
                log.warning(f"[notifier] emit falló ({exc}) — entrega directa")

        try:
            await self.ws_registry.broadcast(event.to_ws_payload())
        except Exception as exc:
            log.debug(f"[notifier] ws broadcast falló: {exc}")

        if not self.channel_manager:
            return
        text = event.payload.get("telegram_text") or event.message or event.title
        try:
            if event.payload.get("broadcast_all"):
                await self.channel_manager.broadcast_text(text)
            else:
                await self._telegram_send(text)
        except Exception as exc:
            log.debug(f"[notifier] envío a canales falló: {exc}")

