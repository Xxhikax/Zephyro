# -*- coding: utf-8 -*-
"""
Frente A: dictado de agenda con rango horario → CREAR (no consultar).

Bug en vivo: "para mañana tengo el curso X de 7 a 9 y media" respondió "no tenés
eventos" — el patrón día+tengo lo mandó a listar sin mirar que había rango
horario. Fix: _has_time_range desambigua dictado (crear) de consulta (listar).
"""
from pathlib import Path

import pytest

from skills.registry import SkillRegistry
from skills.loader import SkillLoader
from skills.builtin.schedule.skill import (
    _has_time_range, _DAY_TENGO_QUERY_RE, _CREATE_VERB_PATTERN,
)
from core.request_context import RequestContext

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def schedule():
    reg = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(reg)
    return reg.get_skill("schedule")


# ── _has_time_range ────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("para mañana tengo el curso diseño de base de datos de 7 hasta las 9 y media", True),
    ("para hoy tengo arquitectura de 7 de la noche hasta las 10", True),
    ("tengo clase de 8 a 10", True),
    ("reunión a las 15:30", True),
    ("qué tengo mañana", False),
    ("mañana tengo parcial", False),
    ("hoy tengo clases", False),
])
def test_has_time_range(text, expected):
    assert _has_time_range(text) is expected, f"{text!r}"


# ── La decisión de routing: el guard de consulta cede ante rango horario ────
def _is_query_route(lower):
    """Replica el guard de handle() línea ~1693: día+tengo, sin verbo de
    creación, SIN rango horario → consulta."""
    return (bool(_DAY_TENGO_QUERY_RE.search(lower))
            and not _CREATE_VERB_PATTERN.search(lower)
            and not _has_time_range(lower))


def test_dictation_with_time_is_not_query():
    lower = "para mañana tengo el curso x de 7 a 9 y media"
    assert _DAY_TENGO_QUERY_RE.search(lower)           # matchea día+tengo
    assert not _CREATE_VERB_PATTERN.search(lower)      # sin "agendá"
    assert not _is_query_route(lower)                  # PERO no es consulta (tiene hora)


def test_query_without_time_is_query():
    lower = "hoy tengo clases"
    assert _is_query_route(lower)                      # sin hora → consulta


def test_reversed_order_tengo_dia_is_query():
    # Bug en vivo (2026-07-04): "Tengo eventos para mañana." (orden tengo→día)
    # no matcheaba _DAY_TENGO_QUERY_RE (solo cubría día→tengo) y caía al chat.
    assert _is_query_route("tengo eventos para mañana.")
    assert _is_query_route("dime si tengo eventos para el día de hoy.")


def test_reversed_order_with_time_is_dictation():
    # El orden inverso respeta el mismo disambiguador: con hora es DICTADO.
    lower = "tengo clase de cálculo mañana de 7 a 9"
    assert _DAY_TENGO_QUERY_RE.search(lower)
    assert not _is_query_route(lower)                  # tiene hora → crear


@pytest.mark.asyncio
async def test_handle_forced_bypasses_matches_command(schedule, monkeypatch):
    """Fase 2: el classifier cloud rutea a schedule aunque matches_command falle
    (forma rara). handle_forced debe saltear el guard y entrar al extractor."""
    if schedule is None:
        pytest.skip("schedule no cargada")
    weird = "fijate qué tengo encima esta semana che"
    assert not schedule.matches_command(weird), "el guard heurístico debe fallar acá"

    captured = {}
    async def fake_extract(user_input, user_id):
        captured["input"] = user_input
        return "AGENDA: tenés 2 eventos esta semana"
    monkeypatch.setattr(schedule, "_extract_schedule_intent", fake_extract)

    ctx = RequestContext(user_role="admin", user_name="Bruno",
                         authenticated=True, session_id="t")
    result = await schedule.handle_forced(weird, ctx)
    assert result == "AGENDA: tenés 2 eventos esta semana"
    assert "input" in captured, "handle_forced debió llamar al extractor LLM"


# ── Integración: el handle NO responde "no tenés eventos" a un dictado ──────
@pytest.mark.asyncio
async def test_dictation_does_not_route_to_listing(schedule):
    if schedule is None:
        pytest.skip("schedule no cargada")
    ctx = RequestContext(user_role="admin", user_name="Bruno",
                         authenticated=True, session_id="t")
    result = await schedule.handle(
        "para hoy tengo el curso de prueba dictado de 8 a 9 de la mañana", ctx)
    # El bug era responder "no tienes/tenés eventos" (listado). No debe pasar.
    assert result is not None
    low = result.lower()
    assert "no tienes eventos" not in low and "no tenés eventos" not in low, \
        f"el dictado se interpretó como consulta: {result!r}"
