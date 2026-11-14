# -*- coding: utf-8 -*-
"""
Falso positivo de schedule: verbos de modificación en CONVERSACIÓN.

Bug en vivo: "El modelo LLM que estás usando CAMBIA tu perspectiva como
asistente?" se ruteó a la agenda → "No encontré el número de evento". Causa:
_MODIFY_PATTERN (cambia/verifica/ajusta…) matcheaba sin exigir contexto de
agenda. Fix: _matches_semantic exige contexto (evento/keyword/temporal) para
los verbos de modify/delete/reschedule.
"""
from pathlib import Path

import pytest

from skills.registry import SkillRegistry
from skills.loader import SkillLoader

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def schedule():
    reg = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(reg)
    return reg.get_skill("schedule")


# ── Conversación con verbos de modificación → NO es agenda ──────────────────
@pytest.mark.parametrize("text", [
    "el modelo llm que estás usando cambia tu perspectiva como asistente en general",
    "qué forma de ver el modelo llm que estés usando cambia tu perspectiva",
    "verifica esto que te digo por favor",
    "eso me cambió la forma de pensar",
    "no pude entender bien la explicación",
    "podés cambiar tu tono de voz",
    "ajustá tu forma de responder un poco",
    "corregí lo que dijiste antes",
])
def test_conversational_modify_is_not_schedule(schedule, text):
    if schedule is None:
        pytest.skip("schedule no cargada")
    assert not schedule.matches_command(text), \
        f"falso positivo: {text!r} se interpretó como agenda"
    assert schedule.match_confidence(text) == 0.0, f"{text!r}"


# ── Modificación REAL de agenda → SÍ es agenda (no-regresión) ───────────────
@pytest.mark.parametrize("text", [
    "modifica evento 13 para que termine a las 11:10",
    "cambia categoría evento 13 a universidad",
    "cambiá la clase de cálculo del lunes",
    "verificá el evento 5",
    "modificá mi reunión de mañana",
    "reprogramá el gym del lunes",
    "pasalo a las 11 de la mañana",
    "borra la clase de cálculo del lunes",
    "elimina el evento 7",
])
def test_real_schedule_modify_still_matches(schedule, text):
    if schedule is None:
        pytest.skip("schedule no cargada")
    assert schedule.matches_command(text), \
        f"se perdió un comando real de agenda: {text!r}"
    assert schedule.match_confidence(text) > 0.0, f"{text!r}"
