# -*- coding: utf-8 -*-
"""Tests del núcleo de notificaciones (Fase 1 del roadmap, ADR-011).

Lo que importa acá no es que "se envíe": es que **NO se envíe** cuando no
corresponde (severidad, condición, horas de silencio, cooldown) y que el dedup
sobreviva un reinicio del proceso. Eso último era imposible con el dict en
memoria de proactive_notifier.
"""
import time

import pytest

from core.notification_center import NotificationCenter
from core.notification_rules import (
    DEFAULT_RULES,
    EVENT_CI_RUN_FAILED,
    EVENT_EXPENSES_BUDGET_THRESHOLD,
    NotificationEvent,
    NotificationRule,
    RuleStore,
)


# Reloj fijo: ver `frozen_notification_clock` en conftest.py para el por qué.
# Sin esto, los tests que no pasan `now=` explícito fallan entre las 23:00 y
# las 08:00 por las horas de silencio de DEFAULT_RULES.
@pytest.fixture(autouse=True)
def _freeze(frozen_notification_clock):
    return frozen_notification_clock


# ── Fakes de canales ─────────────────────────────────────────────────────────

class FakeWSRegistry:
    def __init__(self, fail=False):
        self.sent: list[dict] = []
        self.fail = fail

    async def broadcast(self, payload):
        if self.fail:
            raise RuntimeError("ws caído")
        self.sent.append(payload)


class FakeTelegram:
    def __init__(self, fail=False, interactive=False):
        self.sent: list[tuple] = []
        self.interactive_sent: list[tuple] = []
        self.fail = fail
        if interactive:
            self.send_canvas_interactive = self._send_interactive

    def get_authorized_ids(self):
        return ["telegram:123"]

    async def send(self, cid, text, parse_mode=None):
        if self.fail:
            raise RuntimeError("telegram caído")
        self.sent.append((cid, text, parse_mode))

    async def _send_interactive(self, raw_id, text, keyboard):
        self.interactive_sent.append((raw_id, text, keyboard))


class FakeChannelManager:
    def __init__(self, telegram=None):
        self._tg = telegram

    def get_channel(self, name):
        return self._tg if name == "telegram" else None


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return RuleStore(db_path=str(tmp_path / "notifications.db"), seed_defaults=False)


@pytest.fixture
def seeded_store(tmp_path):
    return RuleStore(db_path=str(tmp_path / "notifications.db"), seed_defaults=True)


@pytest.fixture
def ws():
    return FakeWSRegistry()


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
def center(seeded_store, ws, tg):
    c = NotificationCenter(store=seeded_store)
    c.configure(ws_registry=ws, channel_manager=FakeChannelManager(tg))
    return c


# ── NotificationRule: lógica pura ────────────────────────────────────────────

def test_severity_allows():
    rule = NotificationRule(event_type="x", name="r", min_severity="warn")
    assert rule.severity_allows("warn") is True
    assert rule.severity_allows("urgent") is True
    assert rule.severity_allows("info") is False


def test_severity_unknown_falls_back_to_info():
    rule = NotificationRule(event_type="x", name="r", min_severity="inventada")
    assert rule.min_severity == "info"
    assert rule.severity_allows("info") is True


def test_payload_matches_scalar_and_list():
    rule = NotificationRule(
        event_type="x", name="r", match={"level": ["warn", "over"], "category": "food"}
    )
    assert rule.payload_matches({"level": "over", "category": "food"}) is True
    assert rule.payload_matches({"level": "ok", "category": "food"}) is False
    assert rule.payload_matches({"level": "warn", "category": "taxi"}) is False
    assert rule.payload_matches({"level": "warn"}) is False  # falta category


def test_payload_matches_empty_match_accepts_everything():
    rule = NotificationRule(event_type="x", name="r")
    assert rule.payload_matches({"cualquier": "cosa"}) is True


@pytest.mark.parametrize("hour,expected", [
    (22, False), (23, True), (2, True), (7, True), (8, False), (12, False),
])
def test_quiet_hours_crossing_midnight(hour, expected):
    rule = NotificationRule(event_type="x", name="r", quiet_from=23, quiet_to=8)
    assert rule.in_quiet_hours(hour) is expected


@pytest.mark.parametrize("hour,expected", [
    (8, False), (9, True), (16, True), (17, False),
])
def test_quiet_hours_same_day_window(hour, expected):
    rule = NotificationRule(event_type="x", name="r", quiet_from=9, quiet_to=17)
    assert rule.in_quiet_hours(hour) is expected


def test_quiet_hours_disabled_when_unset_or_equal():
    assert NotificationRule(event_type="x", name="r").in_quiet_hours(3) is False
    assert NotificationRule(
        event_type="x", name="r", quiet_from=5, quiet_to=5
    ).in_quiet_hours(5) is False


# ── NotificationEvent ────────────────────────────────────────────────────────

def test_event_derives_dedup_key_and_normalizes_severity():
    ev = NotificationEvent(event_type="a.b", title="Hola", severity="URGENT")
    assert ev.dedup_key == "a.b:Hola"
    assert ev.severity == "urgent"


def test_event_ws_payload_keeps_frontend_shape():
    ev = NotificationEvent(
        event_type="schedule.event_upcoming", title="Examen", message="En 10 min",
        payload={"event_id": 7, "icon": "📚"},
    )
    p = ev.to_ws_payload()
    assert p["type"] == "reminder"          # lo que ya consume src/chat.js
    assert p["subtype"] == "schedule.event_upcoming"
    assert p["event_id"] == 7 and p["icon"] == "📚"


# ── RuleStore ────────────────────────────────────────────────────────────────

def test_seed_is_idempotent(tmp_path):
    path = str(tmp_path / "n.db")
    s1 = RuleStore(db_path=path)
    first = len(s1.all_rules())
    assert first == len(DEFAULT_RULES)
    assert RuleStore(db_path=path).seed_defaults() == 0
    assert len(RuleStore(db_path=path).all_rules()) == first


def test_seed_does_not_overwrite_user_edits(tmp_path):
    """Si Bruno desactiva una regla, un reinicio no puede revivirla."""
    path = str(tmp_path / "n.db")
    store = RuleStore(db_path=path)
    rule = store.rules_for(EVENT_CI_RUN_FAILED)[0]
    store.set_enabled(rule.id, False)
    RuleStore(db_path=path).seed_defaults()
    assert store.rules_for(EVENT_CI_RUN_FAILED, only_enabled=True) == []


def test_upsert_and_delete(store):
    rule = store.upsert_rule(NotificationRule(
        event_type="x.y", name="propia", min_severity="warn", cooldown_seconds=60,
    ))
    assert rule.id is not None
    store.upsert_rule(NotificationRule(
        event_type="x.y", name="propia", min_severity="urgent",
    ))
    assert len(store.rules_for("x.y")) == 1          # upsert, no duplicado
    assert store.rules_for("x.y")[0].min_severity == "urgent"
    assert store.delete_rule(rule.id) is True
    assert store.rules_for("x.y") == []


def test_event_types_only_enabled(seeded_store):
    types = seeded_store.event_types()
    assert EVENT_CI_RUN_FAILED in types
    rule = seeded_store.rules_for(EVENT_CI_RUN_FAILED)[0]
    seeded_store.set_enabled(rule.id, False)
    assert EVENT_CI_RUN_FAILED not in seeded_store.event_types()


def test_log_survives_a_new_store_instance(tmp_path):
    """El dedup persistente: el equivalente en memoria se perdía al reiniciar."""
    path = str(tmp_path / "n.db")
    RuleStore(db_path=path).record_sent("k1", 1, sent_at=1000.0)
    assert RuleStore(db_path=path).last_sent_at("k1", 1) == 1000.0


def test_purge_log(tmp_path):
    store = RuleStore(db_path=str(tmp_path / "n.db"), seed_defaults=False)
    store.record_sent("viejo", 1, sent_at=time.time() - 40 * 86400)
    store.record_sent("nuevo", 1)
    assert store.purge_log(older_than_seconds=30 * 86400) == 1
    assert store.last_sent_at("nuevo", 1) is not None


# ── Evaluación ───────────────────────────────────────────────────────────────

def test_no_rule_means_no_delivery(center):
    decisions = center.evaluate(NotificationEvent(
        event_type="dominio.inexistente", title="x",
    ))
    assert all(not d.delivered for d in decisions)
    assert "ninguna regla" in decisions[0].reason


def test_budget_ok_level_never_notifies(center):
    """La regla de presupuesto matchea solo warn/over: 'ok' no molesta."""
    decisions = center.evaluate(NotificationEvent(
        event_type=EVENT_EXPENSES_BUDGET_THRESHOLD, title="Presupuesto",
        severity="warn", payload={"level": "ok"},
    ))
    assert all(not d.delivered for d in decisions)
    assert "payload" in decisions[0].reason


def test_budget_over_notifies(center):
    decisions = center.evaluate(NotificationEvent(
        event_type=EVENT_EXPENSES_BUDGET_THRESHOLD, title="Presupuesto",
        severity="warn", payload={"level": "over"},
    ))
    assert any(d.delivered for d in decisions)


def test_severity_below_minimum_is_suppressed(center):
    decisions = center.evaluate(NotificationEvent(
        event_type=EVENT_CI_RUN_FAILED, title="CI", severity="info",
    ))
    assert all(not d.delivered for d in decisions)
    assert "severidad" in decisions[0].reason


def test_quiet_hours_suppress_non_urgent_but_let_urgent_through(store):
    store.upsert_rule(NotificationRule(
        event_type="x.y", name="con silencio", min_severity="info",
        quiet_from=23, quiet_to=8,
    ))
    center = NotificationCenter(store=store)
    # 2026-09-28 03:00 hora de Lima
    from datetime import datetime
    from core.notification_center import LOCAL_TZ
    madrugada = datetime(2026, 9, 28, 3, 0, tzinfo=LOCAL_TZ).timestamp()

    quiet = center.evaluate(NotificationEvent(event_type="x.y", title="t"), now=madrugada)
    assert not quiet[0].delivered and "silencio" in quiet[0].reason

    urgent = center.evaluate(
        NotificationEvent(event_type="x.y", title="t", severity="urgent"), now=madrugada
    )
    assert urgent[0].delivered is True


def test_cooldown_blocks_then_expires(store):
    rule = store.upsert_rule(NotificationRule(
        event_type="x.y", name="con cooldown", cooldown_seconds=600,
    ))
    center = NotificationCenter(store=store)
    ev = NotificationEvent(event_type="x.y", title="t", dedup_key="k")
    t0 = 1_000_000.0

    assert center.evaluate(ev, now=t0)[0].delivered is True
    store.record_sent("k", rule.id, sent_at=t0)

    blocked = center.evaluate(ev, now=t0 + 60)[0]
    assert blocked.delivered is False and "cooldown" in blocked.reason

    assert center.evaluate(ev, now=t0 + 601)[0].delivered is True


# ── Entrega ──────────────────────────────────────────────────────────────────

async def test_emit_delivers_to_both_channels_and_records(center, ws, tg):
    ev = NotificationEvent(
        event_type=EVENT_CI_RUN_FAILED, title="CI fallido", message="run #42",
        severity="warn", dedup_key="ci:42",
    )
    decisions = await center.emit(ev, now=1_000_000.0)
    assert any(d.delivered for d in decisions)
    assert len(ws.sent) == 1 and ws.sent[0]["title"] == "CI fallido"
    assert len(tg.sent) == 1 and "CI fallido" in tg.sent[0][1]
    rule_id = center.store.rules_for(EVENT_CI_RUN_FAILED)[0].id
    assert center.store.last_sent_at("ci:42", rule_id) == 1_000_000.0


async def test_emit_twice_respects_cooldown(center, ws):
    ev = NotificationEvent(
        event_type=EVENT_CI_RUN_FAILED, title="CI fallido", severity="warn",
        dedup_key="ci:42",
    )
    await center.emit(ev, now=1_000_000.0)
    await center.emit(ev, now=1_000_060.0)
    assert len(ws.sent) == 1


async def test_a_dead_channel_does_not_block_the_other(seeded_store):
    tg = FakeTelegram()
    center = NotificationCenter(store=seeded_store)
    center.configure(
        ws_registry=FakeWSRegistry(fail=True), channel_manager=FakeChannelManager(tg)
    )
    decisions = await center.emit(NotificationEvent(
        event_type=EVENT_CI_RUN_FAILED, title="CI", severity="warn",
    ))
    assert any(d.delivered for d in decisions)
    assert len(tg.sent) == 1


async def test_no_channel_available_is_not_recorded_so_it_can_retry(seeded_store):
    """Si nada salió, no se marca enviado: el próximo tick reintenta."""
    center = NotificationCenter(store=seeded_store)
    center.configure(
        ws_registry=FakeWSRegistry(fail=True),
        channel_manager=FakeChannelManager(FakeTelegram(fail=True)),
    )
    ev = NotificationEvent(
        event_type=EVENT_CI_RUN_FAILED, title="CI", severity="warn", dedup_key="k",
    )
    decisions = await center.emit(ev)
    assert all(not d.delivered for d in decisions)
    rule_id = seeded_store.rules_for(EVENT_CI_RUN_FAILED)[0].id
    assert seeded_store.last_sent_at("k", rule_id) is None


async def test_telegram_text_override_preserves_legacy_message(center, tg):
    """Clave para migrar proactive_notifier sin cambiarle el texto a Bruno."""
    await center.emit(NotificationEvent(
        event_type=EVENT_CI_RUN_FAILED, title="ignorado", severity="warn",
        payload={"telegram_text": "⚠️ <b>texto exacto de siempre</b>"},
    ))
    assert tg.sent[0][1] == "⚠️ <b>texto exacto de siempre</b>"


async def test_keyboard_uses_interactive_channel(seeded_store):
    tg = FakeTelegram(interactive=True)
    center = NotificationCenter(store=seeded_store)
    center.configure(ws_registry=FakeWSRegistry(), channel_manager=FakeChannelManager(tg))
    await center.emit(NotificationEvent(
        event_type=EVENT_CI_RUN_FAILED, title="CI", severity="warn",
        payload={"keyboard": [[("ok", "cb:ok")]]},
    ))
    assert len(tg.interactive_sent) == 1
    assert tg.interactive_sent[0][0] == 123        # el "telegram:" se limpia
    assert tg.sent == []


async def test_emit_never_raises_without_sinks(seeded_store):
    center = NotificationCenter(store=seeded_store)
    decisions = await center.emit(NotificationEvent(
        event_type=EVENT_CI_RUN_FAILED, title="CI", severity="warn",
    ))
    assert all(not d.delivered for d in decisions)


# ── Integración con el event_bus existente ───────────────────────────────────

async def test_bus_event_reaches_the_center(seeded_store, ws, tg):
    from core.event_bus import get_bus, reset_bus
    reset_bus()
    center = NotificationCenter(store=seeded_store)
    center.configure(ws_registry=ws, channel_manager=FakeChannelManager(tg))
    assert center.subscribe_to_bus() > 0
    assert center.subscribe_to_bus() == 0          # idempotente

    await center._on_bus_event({
        "event_type": EVENT_CI_RUN_FAILED,
        "title": "CI fallido",
        "severity": "warn",
        "run_id": 99,
    })
    assert len(ws.sent) == 1
    assert ws.sent[0]["run_id"] == 99              # los extras viajan al payload
    reset_bus()


async def test_bus_payload_without_event_type_is_ignored(center, ws):
    await center._on_bus_event({"title": "sin tipo"})
    assert ws.sent == []
