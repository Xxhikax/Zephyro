# -*- coding: utf-8 -*-
"""Productores de eventos de la Fase 1: presupuestos y CI (ADR-011).

Dos cosas se fijan acá:
1. El productor NO decide si notificar — solo declara el hecho. La supresión la
   hacen las reglas (`test_notification_center.py`).
2. El watcher de CI es read-only y opt-in: nada dispara ni re-dispara una corrida.
"""
import json
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.notification_center import NotificationCenter
from core.notification_rules import (
    EVENT_CI_RUN_FAILED,
    EVENT_EXPENSES_BUDGET_THRESHOLD,
    RuleStore,
)
from core.proactive_notifier import ProactiveNotifier
from modules.ci.watcher import CIWatcher
from modules.expenses.service import ExpenseService


# Reloj fijo: ver `frozen_notification_clock` en conftest.py para el por qué.
# Estos productores entregan a través del NotificationCenter real, así que
# entre las 23:00 y las 08:00 las horas de silencio de DEFAULT_RULES
# suprimían la entrega y los tests fallaban.
@pytest.fixture(autouse=True)
def _freeze(frozen_notification_clock):
    return frozen_notification_clock


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
def notifier(store, tg):
    ws = MagicMock()
    ws.broadcast = AsyncMock()
    return ProactiveNotifier(
        schedule_service=MagicMock(),
        reminder_system=None,
        channel_manager=FakeChannelManager(tg),
        ws_registry=ws,
        user_id="admin",
        notification_center=NotificationCenter(store=store),
    )


def _at_notify_window() -> float:
    """Un timestamp dentro de la ventana de 8am (hora local del server)."""
    return datetime.now().replace(hour=8, minute=1, second=0, microsecond=0).timestamp()


# ── Productor: presupuestos ──────────────────────────────────────────────────

@pytest.fixture
def expense_svc(tmp_path, monkeypatch):
    svc = ExpenseService(db_path=str(tmp_path / "expenses.db"))
    import modules.expenses.service as mod
    monkeypatch.setattr(mod, "get_expense_service", lambda: svc)
    return svc


async def test_budget_over_produces_a_notification(notifier, expense_svc, tg):
    expense_svc.set_budget("admin", 100)
    expense_svc.add_expense("admin", 150, "mercado")

    await notifier._check_budgets(_at_notify_window())

    p = notifier.ws_registry.broadcast.await_args.args[0]
    assert p["subtype"] == "expenses_budget"
    assert p["level"] == "over"
    assert p["action"] == "open_expenses"
    assert "excedido" in p["title"]
    assert "S/ 150.00" in tg.sent[0][1]


async def test_budget_ok_produces_nothing(notifier, expense_svc, tg):
    expense_svc.set_budget("admin", 1000)
    expense_svc.add_expense("admin", 10, "café")
    await notifier._check_budgets(_at_notify_window())
    assert notifier.ws_registry.broadcast.await_count == 0
    assert tg.sent == []


async def test_budget_warn_level_notifies_once_per_month(notifier, expense_svc):
    """dedup_key por categoría+nivel+mes + cooldown de 12h de la regla."""
    expense_svc.set_budget("admin", 100)
    expense_svc.add_expense("admin", 85, "mercado")
    now = _at_notify_window()

    await notifier._check_budgets(now)
    assert notifier.ws_registry.broadcast.await_count == 1

    notifier._last_budget_check = 0.0      # salteamos el guard de intervalo
    await notifier._check_budgets(now + 60)
    assert notifier.ws_registry.broadcast.await_count == 1


async def test_budget_check_respects_the_interval_guard(notifier, expense_svc):
    expense_svc.set_budget("admin", 100)
    expense_svc.add_expense("admin", 150, "mercado")
    now = _at_notify_window()
    await notifier._check_budgets(now)
    await notifier._check_budgets(now + 10)   # dentro del intervalo → no consulta
    assert notifier.ws_registry.broadcast.await_count == 1


async def test_budget_check_only_runs_in_the_notify_window(notifier, expense_svc):
    expense_svc.set_budget("admin", 100)
    expense_svc.add_expense("admin", 150, "mercado")
    noon = datetime.now().replace(hour=12, minute=30).timestamp()
    await notifier._check_budgets(noon)
    assert notifier.ws_registry.broadcast.await_count == 0


async def test_budget_failure_does_not_break_the_tick(notifier, monkeypatch):
    import modules.expenses.service as mod

    def boom():
        raise RuntimeError("db caída")

    monkeypatch.setattr(mod, "get_expense_service", boom)
    await notifier._check_budgets(_at_notify_window())   # no debe levantar
    assert notifier.ws_registry.broadcast.await_count == 0


def test_budget_rule_ignores_ok_level(store):
    rule = store.rules_for(EVENT_EXPENSES_BUDGET_THRESHOLD)[0]
    assert rule.payload_matches({"level": "ok"}) is False
    assert rule.payload_matches({"level": "over"}) is True


# ── Productor: CI ────────────────────────────────────────────────────────────

_RUNS = [{
    "databaseId": 4242,
    "workflowName": "tests",
    "displayTitle": "fix: algo",
    "headBranch": "feat/x",
    "conclusion": "failure",
    "createdAt": "2026-09-28T12:00:00Z",
    "url": "https://github.com/o/r/actions/runs/4242",
}]


def _runner(payload, code=0, stderr=""):
    calls: list[list[str]] = []

    async def run(argv):
        calls.append(argv)
        return (code, json.dumps(payload) if isinstance(payload, list) else payload, stderr)

    run.calls = calls
    return run


async def test_ci_poll_builds_one_event_per_failed_run():
    w = CIWatcher(gh_path="gh", runner=_runner(_RUNS))
    events = await w.poll(time.time(), force=True)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == EVENT_CI_RUN_FAILED
    assert ev.severity == "warn"
    assert ev.dedup_key == "ci:run:4242"
    assert ev.payload["run_id"] == 4242
    assert ev.payload["branch"] == "feat/x"
    assert "CI fallido" in ev.payload["telegram_text"]


async def test_ci_poll_is_read_only_and_never_reruns():
    runner = _runner(_RUNS)
    w = CIWatcher(gh_path="gh", runner=runner)
    await w.poll(time.time(), force=True)
    argv = runner.calls[0]
    assert argv[1:3] == ["run", "list"]
    forbidden = {"rerun", "cancel", "delete", "workflow", "dispatch"}
    assert not forbidden.intersection(argv)


async def test_ci_poll_disabled_without_opt_in(monkeypatch):
    monkeypatch.delenv("ZEPHYRO_CI_WATCH", raising=False)
    runner = _runner(_RUNS)
    w = CIWatcher(gh_path="gh", runner=runner)
    assert await w.poll(time.time()) == []
    assert runner.calls == []


async def test_ci_poll_enabled_with_opt_in(monkeypatch):
    monkeypatch.setenv("ZEPHYRO_CI_WATCH", "1")
    w = CIWatcher(gh_path="gh", runner=_runner(_RUNS))
    assert w.is_enabled() is True
    assert len(await w.poll(time.time())) == 1


async def test_ci_poll_respects_interval(monkeypatch):
    monkeypatch.setenv("ZEPHYRO_CI_WATCH", "1")
    runner = _runner(_RUNS)
    w = CIWatcher(gh_path="gh", runner=runner, poll_seconds=900)
    now = time.time()
    await w.poll(now)
    await w.poll(now + 10)
    assert len(runner.calls) == 1
    await w.poll(now + 901)
    assert len(runner.calls) == 2


@pytest.mark.parametrize("payload,code,expected", [
    ([], 0, 0),                        # sin fallos
    ("no es json", 0, 0),              # salida corrupta
    ([], 1, 0),                        # gh falló (repo sin workflows, sin auth…)
    ([{"workflowName": "x"}], 0, 0),   # run sin databaseId
])
async def test_ci_poll_degrades_quietly(payload, code, expected):
    w = CIWatcher(gh_path="gh", runner=_runner(payload, code=code))
    assert len(await w.poll(time.time(), force=True)) == expected


async def test_ci_poll_survives_a_runner_that_raises():
    async def boom(argv):
        raise OSError("gh no está")

    w = CIWatcher(gh_path="gh", runner=boom)
    assert await w.poll(time.time(), force=True) == []


async def test_notifier_emits_ci_events(notifier, monkeypatch, tg):
    fake = SimpleNamespace(poll=AsyncMock(return_value=[
        CIWatcher._to_event(_RUNS[0])
    ]))
    import modules.ci.watcher as mod
    monkeypatch.setattr(mod, "get_ci_watcher", lambda: fake)

    await notifier._check_ci(time.time())

    p = notifier.ws_registry.broadcast.await_args.args[0]
    assert p["subtype"] == "ci_run_failed"
    assert p["run_id"] == 4242
    assert "CI fallido" in tg.sent[0][1]


async def test_notifier_ci_failure_does_not_break_the_tick(notifier, monkeypatch):
    import modules.ci.watcher as mod

    def boom():
        raise RuntimeError("watcher roto")

    monkeypatch.setattr(mod, "get_ci_watcher", boom)
    await notifier._check_ci(time.time())
    assert notifier.ws_registry.broadcast.await_count == 0
