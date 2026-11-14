# -*- coding: utf-8 -*-
"""Paridad del ProactiveNotifier tras migrarlo al NotificationCenter (Fase 1).

El riesgo de esta migración no es que deje de enviar: es que envíe **distinto**
y rompa `src/chat.js` (que rutea por `subtype`) o el texto que Bruno ya recibe en
Telegram. Cada test de acá fija el contrato viejo.

El último grupo prueba lo nuevo: que las reglas efectivamente gobiernen, y que si
el center falla el aviso salga igual por el camino directo.
"""
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.notification_center import NotificationCenter
from core.notification_rules import (
    EVENT_SCHEDULE_MISSED,
    EVENT_SCHEDULE_UPCOMING,
    RuleStore,
)
from core.proactive_notifier import ProactiveNotifier


class FakeTelegram:
    def __init__(self):
        self.sent: list[tuple] = []

    def get_authorized_ids(self):
        return ["telegram:123"]

    async def send(self, cid, text, parse_mode=None):
        self.sent.append((cid, text, parse_mode))


class FakeChannelManager:
    def __init__(self, telegram):
        self._tg = telegram
        self.broadcast_texts: list[str] = []

    def get_channel(self, name):
        return self._tg if name == "telegram" else None

    async def broadcast_text(self, text):
        self.broadcast_texts.append(text)


@pytest.fixture
def store(tmp_path):
    return RuleStore(db_path=str(tmp_path / "notifications.db"))


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
def channels(tg):
    return FakeChannelManager(tg)


@pytest.fixture
def notifier(store, channels):
    ws = MagicMock()
    ws.broadcast = AsyncMock()
    center = NotificationCenter(store=store)
    n = ProactiveNotifier(
        schedule_service=MagicMock(),
        reminder_system=None,
        channel_manager=channels,
        ws_registry=ws,
        user_id="admin",
        notification_center=center,
    )
    assert n.notifications is not None, "el center debería estar activo"
    return n


def _event(ev_id=1, title="Entreno de boxeo", category="fitness", start=None):
    start = start if start is not None else time.time()
    return SimpleNamespace(
        id=ev_id, title=title, category=category, start_ts_utc=start,
        end_ts_utc=start + 3600, notes="", status="pending",
        reminder_profile="fitness",
    )


def _ws_payload(notifier):
    assert notifier.ws_registry.broadcast.await_count == 1
    return notifier.ws_registry.broadcast.await_args.args[0]


# ── Paridad: agenda ──────────────────────────────────────────────────────────

async def test_schedule_pre_stage_keeps_ws_contract(notifier):
    ev = _event()
    await notifier._notify_stage(ev, "pre", 15, "fitness")

    p = _ws_payload(notifier)
    assert p["type"] == "reminder"
    assert p["subtype"] == "schedule_pre_15"   # el frontend rutea por esto
    assert p["event_id"] == ev.id
    assert p["title"] == ev.title
    assert p["category"] == "fitness"
    assert p["category_label"] == "Entreno"
    assert p["icon"] == "🥊"
    assert p["profile"] == "fitness"
    assert p["stage"] == "pre_15"
    assert p["start_ts"] == ev.start_ts_utc
    assert p["action"] == "open_agenda"
    assert "15 min" in p["message"]


async def test_schedule_telegram_text_is_the_legacy_text(notifier, tg):
    ev = _event()
    await notifier._notify_stage(ev, "at_start", 0, "fitness")
    assert len(tg.sent) == 1
    cid, text, parse_mode = tg.sent[0]
    assert cid == "telegram:123"
    assert parse_mode == "HTML"
    # Plantilla exacta de _STAGE_TEMPLATES["fitness"]["at_start"]
    assert text == f"🥊 <b>AHORA</b>: {ev.title}. ¡Vamos!"


async def test_control_keys_do_not_leak_into_ws_payload(notifier):
    await notifier._notify_stage(_event(), "pre", 15, "fitness")
    p = _ws_payload(notifier)
    assert "telegram_text" not in p
    assert "broadcast_all" not in p


async def test_missed_stage_is_warn_and_still_delivers(notifier, store, tg):
    """La regla de eventos perdidos exige severidad warn: si el notifier
    mandara 'info', el aviso se perdería en silencio."""
    await notifier._notify_stage(_event(), "missed", 30, "fitness")
    assert _ws_payload(notifier)["subtype"] == "schedule_missed"
    assert len(tg.sent) == 1
    assert store.rules_for(EVENT_SCHEDULE_MISSED)[0].min_severity == "warn"


# ── Paridad: recordatorios ───────────────────────────────────────────────────

async def test_reminder_keeps_ws_contract_and_text(notifier, tg):
    r = SimpleNamespace(id=9, message="Llamar al dentista", due_at=time.time() + 300)
    await notifier._notify_reminder(r, time.time())

    p = _ws_payload(notifier)
    assert p["subtype"] == "reminder"
    assert p["reminder_id"] == 9
    assert p["title"] == "Llamar al dentista"
    assert p["icon"] == "⏰"
    assert p["category_label"] == "Recordatorio"
    assert p["action"] == "open_reminders"
    assert p["mins_remaining"] == 5

    assert tg.sent[0][1] == (
        "⏰ <b>Recordatorio</b>\n\n<b>Llamar al dentista</b>\n⏰ En 5 minutos"
    )


async def test_reminder_now_uses_the_now_wording(notifier, tg):
    r = SimpleNamespace(id=10, message="Salir", due_at=time.time() - 10)
    await notifier._notify_reminder(r, time.time())
    assert _ws_payload(notifier)["message"] == "AHORA"
    assert "🔴 <b>AHORA</b>" in tg.sent[0][1]


# ── Paridad: briefing ────────────────────────────────────────────────────────

async def test_briefing_keeps_its_own_ws_shape_and_all_channels(notifier, channels, tg):
    from datetime import datetime

    notifier.briefing_skill = SimpleNamespace(
        generate_briefing=AsyncMock(return_value="Hoy tenés 2 clases.")
    )
    now = time.time()
    notifier.set_briefing_hour(datetime.fromtimestamp(now).hour)

    await notifier._check_briefing(now)

    p = _ws_payload(notifier)
    assert p["type"] == "briefing"          # NO es un reminder
    assert p["text"] == "Hoy tenés 2 clases."
    # Sale por TODOS los canales (WhatsApp incluido), como antes de la migración
    assert channels.broadcast_texts == ["Hoy tenés 2 clases."]
    assert tg.sent == []


# ── Lo nuevo: las reglas gobiernan ───────────────────────────────────────────

async def test_disabling_a_rule_silences_the_notification(notifier, store, tg):
    rule = store.rules_for(EVENT_SCHEDULE_UPCOMING)[0]
    store.set_enabled(rule.id, False)
    await notifier._notify_stage(_event(), "pre", 15, "fitness")
    assert notifier.ws_registry.broadcast.await_count == 0
    assert tg.sent == []


async def test_dedup_survives_a_new_notifier_instance(store, channels, tg, tmp_path):
    """El equivalente en memoria (_notified) se perdía al reiniciar: un evento
    con cooldown se volvía a notificar. Con el log en SQLite, no."""
    rule = store.rules_for(EVENT_SCHEDULE_UPCOMING)[0]
    rule.cooldown_seconds = 3600
    store.upsert_rule(rule)

    def build():
        ws = MagicMock()
        ws.broadcast = AsyncMock()
        return ProactiveNotifier(
            schedule_service=MagicMock(), reminder_system=None,
            channel_manager=channels, ws_registry=ws, user_id="admin",
            notification_center=NotificationCenter(store=RuleStore(
                db_path=store._db_path, seed_defaults=False)),
        )

    ev = _event()
    first = build()
    await first._notify_stage(ev, "pre", 15, "fitness")
    assert first.ws_registry.broadcast.await_count == 1

    second = build()   # "reinicio"
    await second._notify_stage(ev, "pre", 15, "fitness")
    assert second.ws_registry.broadcast.await_count == 0


async def test_falls_back_to_direct_delivery_if_the_center_breaks(store, channels, tg):
    """Perder un aviso es peor que notificar sin reglas."""
    class BrokenCenter(NotificationCenter):
        def configure(self, *a, **kw):
            pass

        async def emit(self, event, now=None):
            raise RuntimeError("store caído")

    ws = MagicMock()
    ws.broadcast = AsyncMock()
    n = ProactiveNotifier(
        schedule_service=MagicMock(), reminder_system=None,
        channel_manager=channels, ws_registry=ws, user_id="admin",
        notification_center=BrokenCenter(store=store),
    )
    await n._notify_stage(_event(), "pre", 15, "fitness")
    assert ws.broadcast.await_count == 1
    assert len(tg.sent) == 1


async def test_notifier_survives_a_center_that_cannot_be_built(monkeypatch, channels):
    """Si la DB de reglas no abre, el notifier igual arranca."""
    import core.notification_center as nc

    def boom():
        raise RuntimeError("sqlite locked")

    monkeypatch.setattr(nc, "get_notification_center", boom)
    ws = MagicMock()
    ws.broadcast = AsyncMock()
    n = ProactiveNotifier(
        schedule_service=MagicMock(), reminder_system=None,
        channel_manager=channels, ws_registry=ws, user_id="admin",
    )
    assert n.notifications is None
    await n._notify_stage(_event(), "pre", 15, "fitness")
    assert ws.broadcast.await_count == 1
