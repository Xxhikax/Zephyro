# -*- coding: utf-8 -*-
"""
Regresión del crash de _handle_reschedule (barrido NLP 2026-07-02).

Bug: el `return "\\n".join(lines)` quedó FUERA del else del branch
multi-candidato, así que con exactamente 1 candidato (el caso más común)
`lines` nunca se definía → UnboundLocalError. La lógica de búsqueda de slots
libres que debía correr para ese branch estaba varada como código muerto
inalcanzable dentro de _pomodoro_break (tras su return) — sus variables
(now/event/duration/user_id/service) calzan 1:1 con el scope de
_handle_reschedule, confirmando que fue un accidente de edición.
"""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from skills.registry import SkillRegistry
from skills.loader import SkillLoader

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def reg():
    r = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(r)
    return r


def _fake_event(event_id, title, start, end):
    ev = MagicMock()
    ev.id = event_id
    ev.title = title
    # _CATEGORY_ALIASES maps "gym" -> "exercise" (not "fitness").
    ev.category = "exercise"
    ev.status = "pending"
    ev.start_ts_utc = start.timestamp()
    ev.end_ts_utc = end.timestamp()
    return ev


def test_reschedule_single_candidate_no_crash(reg, monkeypatch):
    schedule = reg.get_skill("schedule")
    if schedule is None:
        pytest.skip("schedule no cargó")

    now = datetime.now()
    event = _fake_event(14, "Gym", now + timedelta(hours=2), now + timedelta(hours=3))
    service = MagicMock()
    service.get_events_in_range.return_value = [event]
    service.find_free_slots.return_value = [{
        "start": (now + timedelta(days=1, hours=2)).timestamp(),
        "end": (now + timedelta(days=1, hours=3)).timestamp(),
    }]
    monkeypatch.setattr(schedule, "_get_service", lambda: service)

    # Antes del fix: UnboundLocalError (lines nunca asignada con 1 candidato).
    result = schedule._handle_reschedule("reprográmame el gym", "admin")
    assert "slots libres" in result.lower()


def test_reschedule_single_candidate_no_slots(reg, monkeypatch):
    schedule = reg.get_skill("schedule")
    if schedule is None:
        pytest.skip("schedule no cargó")

    now = datetime.now()
    event = _fake_event(14, "Gym", now + timedelta(hours=2), now + timedelta(hours=3))
    service = MagicMock()
    service.get_events_in_range.return_value = [event]
    service.find_free_slots.return_value = []
    monkeypatch.setattr(schedule, "_get_service", lambda: service)

    result = schedule._handle_reschedule("reprográmame el gym", "admin")
    assert "no encontré slots" in result.lower()


def test_reschedule_multi_candidate_returns_list(reg, monkeypatch):
    schedule = reg.get_skill("schedule")
    if schedule is None:
        pytest.skip("schedule no cargó")

    now = datetime.now()
    ev1 = _fake_event(14, "Gym", now + timedelta(hours=2), now + timedelta(hours=3))
    ev2 = _fake_event(15, "Boxeo", now + timedelta(hours=5), now + timedelta(hours=6))
    service = MagicMock()
    service.get_events_in_range.return_value = [ev1, ev2]
    monkeypatch.setattr(schedule, "_get_service", lambda: service)

    result = schedule._handle_reschedule("reprográmame el entreno de la tarde", "admin")
    assert "¿cuál de estos eventos" in result.lower()
    # No debe intentar buscar slots si todavía no se eligió evento.
    service.find_free_slots.assert_not_called()
