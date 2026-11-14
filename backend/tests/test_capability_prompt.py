# -*- coding: utf-8 -*-
"""
Frente C: el prompt del chat no debe inducir alucinación de capacidades.

Bug en vivo: el LLM inventó "problema técnico con schedule" / "no tengo función
para agendar" — porque el bloque CAPACIDADES hardcodeado no incluía agenda y el
LLM no sabía que podía. Fix: conectar capacidades con las skills del registry +
regla explícita anti-alucinación de estado.
"""
import pytest

from core.context_manager import ContextManager


@pytest.fixture(scope="module")
def cm():
    return ContextManager(use_vector_memory=False)


def _build(cm, skills="- schedule: Gestión de agenda y horarios\n- reminders: Recordatorios"):
    return cm.build_prompt("agéndame algo el lunes", skill_summaries=skills)


def test_capabilities_mention_agenda(cm):
    p = _build(cm)
    assert "AGENDA/HORARIOS" in p, "el prompt no afirma la capacidad de agenda"


def test_skill_summaries_injected(cm):
    p = _build(cm)
    assert "HABILIDADES DISPONIBLES" in p
    assert "Gestión de agenda" in p  # la skill del registry aparece


def test_anti_hallucination_rule_present(cm):
    p = _build(cm)
    assert "NUNCA inventes que una skill" in p
    assert "problemas técnicos" in p
    # debe guiar al comando directo, no afirmar que está roto
    assert "comando directo" in p


def test_no_skills_still_builds(cm):
    # Sin skills no debe romper (skill_summaries vacío).
    p = cm.build_prompt("hola", skill_summaries="")
    assert isinstance(p, str) and len(p) > 0


def test_capabilities_assert_web_search(cm):
    """El prompt debe afirmar la capacidad de búsqueda web (bug #7/#9/#10:
    'buscar noticias de hoy' → 'No puedo buscar en internet')."""
    p = cm.build_prompt("buscar noticias de hoy")
    low = p.lower()
    assert "búsqueda web" in low or "tavily" in low, \
        "el prompt no afirma la capacidad de búsqueda web"
    # La regla anti-negación de capacidad debe estar presente
    assert "nunca niegues una capacidad" in low
