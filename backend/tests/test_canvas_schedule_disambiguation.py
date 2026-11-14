# -*- coding: utf-8 -*-
"""
Desambiguación Canvas ↔ Agenda (bidireccional).

Bug en vivo: "revisá si tengo pendientes en Canvas" (tareas del LMS) la tomó la
agenda → "No encontré un curso llamado 'canvas.'".

Causas raíz:
- canvas declaraba `requires.env` → el loader lo descartaba si Canvas no estaba
  configurado, así que su dominio se escapaba a schedule (Fix A: quitar env).
- el forced dispatch de schedule (handle_forced) bypassa matches_command y mangeaba
  la query (Fix B: rechazar dominio Canvas en handle_forced).
- _COURSE_DETAIL_RE de canvas ("qué tengo en X") robaba "qué tengo en la semana"
  (Fix C: guard de contexto-agenda en canvas.matches_command).
"""
from pathlib import Path

import pytest

from skills.registry import SkillRegistry
from skills.loader import SkillLoader

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def reg():
    r = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(r)
    return r


def test_canvas_registers_without_env(reg):
    # Fix A: canvas ya no requiere env → se registra aunque Canvas no esté configurado.
    assert reg.get_skill("canvas") is not None


@pytest.mark.parametrize("text", [
    "revisá si tengo pendientes en canvas",
    "qué tengo en canvas",
    "tareas de canvas",
    "cuál es mi nota en redes",
    "qué tengo en cálculo",
])
def test_canvas_claims_its_domain(reg, text):
    canvas = reg.get_skill("canvas")
    if canvas is None:
        pytest.skip("canvas no cargó")
    assert canvas.matches_command(text) is True


@pytest.mark.parametrize("text", [
    "qué tengo en la semana",
    "qué tengo en la agenda",
    "qué tengo hoy",
    "qué tengo mañana",
])
def test_canvas_does_not_steal_agenda(reg, text):
    canvas = reg.get_skill("canvas")
    if canvas is None:
        pytest.skip("canvas no cargó")
    assert canvas.matches_command(text) is False


@pytest.mark.parametrize("text", [
    "tengo entregas faltantes de redes",
    "cuál es mi nota en redes",
    "pendientes de cálculo",
    "revisá si tengo pendientes en canvas",
])
def test_schedule_rejects_canvas_domain(reg, text):
    schedule = reg.get_skill("schedule")
    if schedule is None:
        pytest.skip("schedule no cargó")
    assert schedule.matches_command(text) is False


@pytest.mark.parametrize("text", [
    "agendá una reunión el martes a las 5",
    "agenda examen de cálculo el lunes a las 9",
])
def test_schedule_keeps_its_commands(reg, text):
    schedule = reg.get_skill("schedule")
    if schedule is None:
        pytest.skip("schedule no cargó")
    assert schedule.matches_command(text) is True


# ── Response-level (barrido NLP 2026-07-02): puntuación STT, bareword canvas,
#    tolerancia de insertos en pendientes, forced dispatch ─────────────────────

import asyncio
from datetime import datetime, timedelta, timezone


class _StubClient:
    def is_configured(self):
        return True

    async def get_course_grades(self):
        return [{"course": "Redes y Comunicaciones", "score": 85.0, "grade": "B"}]

    async def get_missing_submissions(self):
        return []

    async def get_announcements(self, limit=10):
        return []

    async def get_upcoming_assignments(self, days=7):
        return [{
            "title": "Tarea 3",
            "course": "Redes y Comunicaciones",
            "due_at": datetime.now(tz=timezone.utc) + timedelta(days=2),
        }]

    async def get_today_calendar_events(self):
        return []

    async def get_courses(self):
        return [{"id": 1, "name": "Redes y Comunicaciones"}]


def _canvas_with_stub(reg, monkeypatch):
    canvas = reg.get_skill("canvas")
    if canvas is None:
        pytest.skip("canvas no cargó")
    monkeypatch.setattr(canvas, "_get_client", lambda: _StubClient())
    canvas._cache.clear()
    return canvas


def test_c1_stt_trailing_punctuation_not_a_course(reg, monkeypatch):
    # Bug en vivo: "revisa si tengo pendientes en Canvas." → curso 'canvas.'
    canvas = _canvas_with_stub(reg, monkeypatch)
    result = asyncio.run(canvas.handle("revisa si tengo pendientes en canvas.", ctx=None))
    assert result is not None
    assert "no encontré un curso" not in result.lower()


def test_c1_course_hint_strips_question_mark(reg, monkeypatch):
    canvas = _canvas_with_stub(reg, monkeypatch)
    result = asyncio.run(canvas.handle("¿tengo pendientes en redes?", ctx=None))
    assert result is not None
    assert "redes y comunicaciones" in result.lower()


def test_c2_bareword_canvas_gives_summary_not_error(reg, monkeypatch):
    # Trigger documentado en SKILL.md que devolvía "No encontré un curso 'canvas'".
    canvas = _canvas_with_stub(reg, monkeypatch)
    result = asyncio.run(canvas.handle("qué tengo en canvas", ctx=None))
    assert result is not None
    assert "no encontré un curso" not in result.lower()


@pytest.mark.parametrize("text", [
    "tengo pendientes o exámenes en redes",
    "tengo tareas pendientes o faltantes en cálculo",
    "tengo pendientes esta semana en redes",
])
def test_d_pending_query_tolerates_inserted_words(reg, text):
    canvas = reg.get_skill("canvas")
    if canvas is None:
        pytest.skip("canvas no cargó")
    assert canvas.matches_command(text) is True


def test_d_pending_agenda_target_not_stolen(reg):
    # El lookahead evita que el REGEX de pendientes robe "pendientes ... en mi
    # agenda" (de schedule). Nota: la frase no debe EMPEZAR con "tengo
    # pendientes" — ese es un trigger startswith del SKILL.md (pre-existente)
    # que matchea antes de llegar al regex.
    canvas = reg.get_skill("canvas")
    if canvas is None:
        pytest.skip("canvas no cargó")
    assert canvas.matches_command(
        "revisa si tengo pendientes esta semana en mi agenda"
    ) is False


def test_d2_handle_forced_bypasses_matches_command(reg, monkeypatch):
    # El clasificador cloud fuerza canvas para una frase que matches_command
    # rechaza — el override debe despachar igual (antes devolvía None).
    canvas = _canvas_with_stub(reg, monkeypatch)
    phrase = "fijate si tengo algo para entregar en ese curso"
    assert canvas.matches_command(phrase) is False
    result = asyncio.run(canvas.handle_forced(phrase, ctx=None))
    assert result is not None
