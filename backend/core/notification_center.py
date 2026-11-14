"""NotificationCenter — único punto que decide y entrega notificaciones.

Fase 1 del roadmap de skills (ADR-011).

Antes: cada checker de ``core/proactive_notifier.py`` armaba su payload y
llamaba a mano a ``ws_registry.broadcast`` + ``_telegram_send``, con el umbral
hardcodeado y el dedup en un dict en memoria. Cada productor nuevo (gastos, CI,
Gmail…) tenía que repetir las tres cosas.

Ahora hay un solo camino:

    productor → NotificationEvent → NotificationCenter → reglas → canales

El productor solo declara QUÉ pasó. Las reglas
(``core/notification_rules.py``) deciden si sale, por dónde y cada cuánto, y el
log en SQLite hace que el dedup sobreviva un reinicio.

Se integra con el ``core/event_bus.py`` que ya existía: ``subscribe_to_bus()``
registra un listener por cada tipo de evento que tenga reglas, así un productor
puede publicar con ``get_bus().publish(...)`` sin conocer al center. También
puede llamarse ``await get_notification_center().emit(event)`` directo, que es
el camino que usa el notifier.

El center NUNCA levanta una excepción hacia el productor: un canal caído no
puede tumbar el loop que lo llamó.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from core.notification_rules import (
    CHANNEL_ALL,
    CHANNEL_TELEGRAM,
    CHANNEL_WS,
    NotificationEvent,
    NotificationRule,
    RuleStore,
    get_rule_store,
)

log = logging.getLogger("zephyro.notifications")

LOCAL_TZ = ZoneInfo("America/Lima")


@dataclass
class Decision:
    """Qué se resolvió para una regla dada. `reason` existe para poder
    debuggear por qué algo NO se notificó, que es la pregunta difícil."""
    rule_name: str
    rule_id: Optional[int]
    delivered: bool
    reason: str
    channels: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover — solo para logs
        state = "enviado" if self.delivered else "suprimido"
        return f"{self.rule_name}: {state} ({self.reason})"


class NotificationCenter:

    def __init__(self, store: Optional[RuleStore] = None):
        self._store = store or get_rule_store()
        self._ws_registry: Any = None
        self._channel_manager: Any = None
        self._subscribed: set[str] = set()

    # ── Configuración ─────────────────────────────────────────────────────

    def configure(self, ws_registry: Any = None, channel_manager: Any = None) -> None:
        """Inyecta los sinks. Los llama ProactiveNotifier en su __init__, que ya
        los recibe de web_app — así no hace falta tocar web_app.py."""
        if ws_registry is not None:
            self._ws_registry = ws_registry
        if channel_manager is not None:
            self._channel_manager = channel_manager

    @property
    def store(self) -> RuleStore:
        return self._store

    def subscribe_to_bus(self) -> int:
        """Suscribe el center al event_bus para cada tipo de evento con reglas.

        Idempotente: el bus ignora un listener ya registrado y acá además se
        lleva la cuenta, así llamarlo de nuevo tras editar reglas solo agrega
        los tipos nuevos.
        """
        try:
            from core.event_bus import get_bus
        except ImportError:  # pragma: no cover — event_bus es parte del core
            log.warning("[notifications] event_bus no disponible")
            return 0

        bus = get_bus()
        added = 0
        for event_type in self._store.event_types(only_enabled=True):
            if event_type in self._subscribed:
                continue
            bus.subscribe(event_type, self._on_bus_event)
            self._subscribed.add(event_type)
            added += 1
        if added:
            log.info("[notifications] suscrito a %d tipos de evento del bus", added)
        return added

    async def _on_bus_event(self, payload: dict) -> None:
        """Listener del bus. Acepta el payload crudo y lo normaliza a evento."""
        try:
            event = self._event_from_payload(payload)
        except Exception as exc:
            log.warning("[notifications] payload de bus inválido: %s", exc)
            return
        if event is not None:
            await self.emit(event)

    @staticmethod
    def _event_from_payload(payload: dict) -> Optional[NotificationEvent]:
        if isinstance(payload, NotificationEvent):
            return payload
        if not isinstance(payload, dict):
            return None
        event_type = payload.get("event_type")
        if not event_type:
            return None
        known = {"event_type", "title", "message", "severity", "dedup_key", "payload"}
        extra = {k: v for k, v in payload.items() if k not in known}
        extra.update(payload.get("payload") or {})
        return NotificationEvent(
            event_type=event_type,
            title=payload.get("title", ""),
            message=payload.get("message", ""),
            severity=payload.get("severity", "info"),
            dedup_key=payload.get("dedup_key"),
            payload=extra,
        )

    # ── Evaluación ────────────────────────────────────────────────────────

    def evaluate(
        self, event: NotificationEvent, now: Optional[float] = None
    ) -> list[Decision]:
        """Resuelve el evento contra sus reglas SIN entregar nada.

        Función pura respecto de los canales: es el corazón testeable de la
        Fase 1. El único efecto de lectura es el log de entregas (cooldown).
        """
        ts = now if now is not None else time.time()
        rules = self._store.rules_for(event.event_type, only_enabled=True)
        if not rules:
            return [Decision(
                rule_name="(sin regla)", rule_id=None, delivered=False,
                reason=f"ninguna regla habilitada para {event.event_type}",
            )]

        hour = datetime.fromtimestamp(ts, LOCAL_TZ).hour
        decisions: list[Decision] = []
        for rule in rules:
            decisions.append(self._evaluate_one(rule, event, ts, hour))
        return decisions

    def _evaluate_one(
        self, rule: NotificationRule, event: NotificationEvent, ts: float, hour: int
    ) -> Decision:
        def no(reason: str) -> Decision:
            return Decision(rule.name, rule.id, False, reason)

        if not rule.severity_allows(event.severity):
            return no(f"severidad {event.severity} < mínima {rule.min_severity}")
        if not rule.payload_matches(event.payload):
            return no("el payload no matchea la condición de la regla")
        if rule.in_quiet_hours(hour) and event.severity != "urgent":
            return no(f"horas de silencio ({rule.quiet_from}-{rule.quiet_to}h), no urgente")
        if rule.cooldown_seconds > 0 and rule.id is not None:
            last = self._store.last_sent_at(event.dedup_key or "", rule.id)
            if last is not None and (ts - last) < rule.cooldown_seconds:
                remaining = int(rule.cooldown_seconds - (ts - last))
                return no(f"cooldown activo, faltan {remaining}s")
        return Decision(rule.name, rule.id, True, "regla cumplida", list(rule.channels))

    # ── Entrega ───────────────────────────────────────────────────────────

    async def emit(
        self, event: NotificationEvent, now: Optional[float] = None
    ) -> list[Decision]:
        """Evalúa y entrega. Devuelve una decisión por regla evaluada."""
        ts = now if now is not None else time.time()
        decisions = self.evaluate(event, ts)

        for decision in decisions:
            if not decision.delivered:
                log.debug("[notifications] %s", decision)
                continue
            sent_any = await self._deliver(event, decision.channels)
            if sent_any:
                if decision.rule_id is not None:
                    try:
                        self._store.record_sent(event.dedup_key or "", decision.rule_id, ts)
                    except Exception as exc:
                        log.warning("[notifications] no pude registrar la entrega: %s", exc)
            else:
                # Ningún canal aceptó: no se marca como enviado, así el próximo
                # tick puede reintentar en vez de comerse el aviso.
                decision.delivered = False
                decision.reason = "ningún canal disponible"
                log.warning("[notifications] %s", decision)
        return decisions

    async def _deliver(self, event: NotificationEvent, channels: list[str]) -> bool:
        """Entrega por cada canal pedido. True si al menos uno aceptó.

        Un canal que falla no impide el otro: es el mismo criterio que ya tenía
        proactive_notifier con sus try/except por sink.
        """
        sent_any = False
        if CHANNEL_WS in channels:
            sent_any = await self._send_ws(event) or sent_any
        if CHANNEL_TELEGRAM in channels:
            sent_any = await self._send_telegram(event) or sent_any
        if CHANNEL_ALL in channels:
            sent_any = await self._send_all_channels(event) or sent_any
        return sent_any

    async def _send_all_channels(self, event: NotificationEvent) -> bool:
        """Broadcast por todos los canales registrados (Telegram + WhatsApp…)."""
        if self._channel_manager is None:
            return False
        try:
            await self._channel_manager.broadcast_text(self._telegram_text(event))
            return True
        except Exception as exc:
            log.warning("[notifications] broadcast_text falló: %s", exc)
            return False

    async def _send_ws(self, event: NotificationEvent) -> bool:
        if self._ws_registry is None:
            return False
        try:
            await self._ws_registry.broadcast(event.to_ws_payload())
            return True
        except Exception as exc:
            log.debug("[notifications] ws broadcast falló: %s", exc)
            return False

    async def _send_telegram(self, event: NotificationEvent) -> bool:
        if self._channel_manager is None:
            return False
        try:
            tg = self._channel_manager.get_channel("telegram")
        except Exception as exc:
            log.debug("[notifications] get_channel falló: %s", exc)
            return False
        if not tg:
            return False

        text = self._telegram_text(event)
        keyboard = event.payload.get("keyboard")
        sent_any = False
        try:
            authorized = tg.get_authorized_ids()
        except Exception as exc:
            log.debug("[notifications] get_authorized_ids falló: %s", exc)
            return False

        for cid in authorized:
            try:
                if keyboard and hasattr(tg, "send_canvas_interactive"):
                    raw_id = int(str(cid).replace("telegram:", ""))
                    await tg.send_canvas_interactive(raw_id, text, keyboard)
                else:
                    await tg.send(cid, text, parse_mode="HTML")
                sent_any = True
            except Exception as exc:
                log.warning("[notifications] envío a telegram %s falló: %s", cid, exc)
        return sent_any

    @staticmethod
    def _telegram_text(event: NotificationEvent) -> str:
        """`payload['telegram_text']` gana siempre.

        Es lo que permite migrar los avisos que ya existen (agenda, Canvas)
        sin cambiarle ni un carácter al mensaje que Bruno viene recibiendo.
        """
        override = event.payload.get("telegram_text")
        if override:
            return str(override)
        if event.message:
            return f"<b>{event.title}</b>\n{event.message}"
        return f"<b>{event.title}</b>"


_center: Optional[NotificationCenter] = None
_center_lock = threading.Lock()


def get_notification_center() -> NotificationCenter:
    global _center
    if _center is None:
        with _center_lock:
            if _center is None:
                _center = NotificationCenter()
    return _center


def reset_notification_center() -> None:
    """Resetea el singleton — para aislar tests."""
    global _center
    _center = None
