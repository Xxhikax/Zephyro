# -*- coding: utf-8 -*-
"""
Consistencia de verbos voseo a través de TODAS las capas.

Este es el test que PREVIENE el whack-a-mole: una matriz de comandos voseo
representativos (uno por dominio) verificada en cada capa relevante. Si una
capa nueva no reconoce un verbo voseo, este test lo caza.

Capas probadas:
  - intent_detector.classify_by_rules → "command" (pre-router)
  - IntentRouter.classify_and_parse (skip_llm=True = cloud mode heurístico)
  - _RE_CLOUD_AUTOMATION (filtro de cloud mode en orchestrator)
  - _extract_media_entities (extracción de query de media)
  - SkillRegistry.rank_candidates (matching de skills)

Estado inicial (TDD): algunas capas SIN voseo fallarán hasta migrar a la
fuente única core/verbs.py. Eso es esperado y guía la migración.
"""
from pathlib import Path

import pytest

from core.intent_detector import classify_by_rules
from core.router import IntentRouter, _extract_media_entities
from core.orchestrator import _RE_CLOUD_AUTOMATION
from skills.registry import SkillRegistry
from skills.loader import SkillLoader

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def registry():
    reg = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(reg)
    return reg


def _router():
    return IntentRouter(None, None)


# ── Capa: intent_detector.classify_by_rules ────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "abrí discord", "poné lofi", "buscá gatos", "cerrá spotify",
    "reproducí algo", "ejecutá el comando",
])
def test_intent_detector_recognizes_voseo_command(cmd):
    # classify_by_rules reconoce tuteo ("abre discord"→command) pero debe
    # reconocer voseo igual ("abrí discord"→command). Capturar el gap real.
    assert classify_by_rules(cmd) == "command", \
        f"{cmd!r} → {classify_by_rules(cmd)!r} (esperado 'command')"


# ── Capa: router (cloud mode skip_llm) ─────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [
    "abrí discord", "poné lofi en youtube music", "cerrá spotify",
])
async def test_router_cloud_mode_voseo_automation(cmd):
    r = _router()
    a = await r.classify_and_parse(cmd, skip_llm=True)
    assert a.get("intent") in ("automation", "search", "web_search"), \
        f"{cmd!r} → {a.get('intent')} (esperado automation)"


# ── Capa: _RE_CLOUD_AUTOMATION ─────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "abrí discord", "poné lofi en youtube music", "buscá gatos",
    "cerrá spotify", "reproducí algo", "ejecutá ls", "lanzá steam",
    "iniciá el server", "navegá a github",
])
def test_cloud_automation_regex_voseo(cmd):
    assert _RE_CLOUD_AUTOMATION.search(cmd.lower()), \
        f"_RE_CLOUD_AUTOMATION no matchea {cmd!r}"


# ── Capa: extracción de query de media ─────────────────────────────────────
@pytest.mark.parametrize("cmd,expected_query", [
    ("poné lofi en youtube music", "lofi"),
    ("reproducí bohemian rhapsody en youtube", "bohemian rhapsody"),
    ("poné rock en spotify", "rock"),
    ("buscá jazz en youtube music", "jazz"),
])
def test_media_query_extraction_voseo(cmd, expected_query):
    m = _extract_media_entities(cmd)
    assert m is not None, f"{cmd!r} no detectado como media"
    assert m["search_query"] == expected_query, \
        f"{cmd!r} → query {m['search_query']!r} (esperado {expected_query!r})"


# ── Capa: matching de skills ───────────────────────────────────────────────
@pytest.mark.parametrize("skill_name,cmd", [
    ("schedule", "agendá cálculo el lunes a las 7"),
    ("reminders", "recordame comprar pan"),
    ("file-ops", "leé el archivo test.py"),
    ("github", "buscá repo zephyro"),
    ("terminal", "ejecutá el comando ls"),
    ("log-reader", "revisá los logs"),
])
def test_skill_matching_voseo(registry, skill_name, cmd):
    if registry.get_skill(skill_name) is None:
        pytest.skip(f"{skill_name} no cargada")
    names = [c.name for c in registry.rank_candidates(cmd)]
    assert skill_name in names, f"{skill_name} no rankeó para {cmd!r}; got {names}"


# ── Casos negativos: charla casual / pasado NO debe dispararse ─────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "qué aburrido está el día",
    "ya abriste discord",          # pasado 2da persona
    "la verdad no sé qué hacer",
])
async def test_casual_not_automation(text):
    r = _router()
    a = await r.classify_and_parse(text, skip_llm=True)
    assert a.get("intent") != "automation", \
        f"{text!r} no debería ser automation, fue {a.get('intent')}"


# ── Mixto tuteo/voseo en una misma sesión ──────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "abre discord", "abrí discord",      # tuteo y voseo deben ambos andar
    "pon lofi", "poné lofi",
])
def test_tuteo_and_voseo_both_recognized(cmd):
    assert _RE_CLOUD_AUTOMATION.search(cmd.lower()), \
        f"{cmd!r} debería matchear (tuteo y voseo equivalentes)"
