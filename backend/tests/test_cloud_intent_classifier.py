# -*- coding: utf-8 -*-
"""
Classifier registry-aware en cloud mode (Fase 2) — unit.

classify_intent recibe el catálogo DINÁMICO de skills y decide route. El motor
LLM (_call_extractor_llm) se mockea — no se llama API real. Cubre:
- ruteo a skill por forma libre que la heurística no cubre
- auto-descubrimiento: una skill nueva en el catálogo se rutea sin tocar código
- validación de nombres: skill inexistente → chat (None)
- parser robusto: fences markdown, JSON inválido → None
"""
import types
import pytest

import core.cloud_intent_classifier as clf
from core.cloud_intent_classifier import (
    classify_intent, _parse_classification, _skill_names_from_catalog,
)

_CATALOG = (
    "- schedule: Gestión de agenda y horarios (clases, estudio, ejercicio)\n"
    "- reminders: Recordatorios puntuales\n"
    "- system_monitor: Estado del sistema (CPU, RAM, disco)"
)


def _fake_llm():
    # Solo necesita exponer `generate` para pasar el guard de disponibilidad.
    return types.SimpleNamespace(generate=lambda *a, **k: "")


def _patch_llm_response(monkeypatch, raw):
    async def fake_call(llm, prompt):
        return raw
    monkeypatch.setattr(clf, "_call_extractor_llm", fake_call)


# ── parser ──────────────────────────────────────────────────────────────────
def test_skill_names_from_catalog():
    assert _skill_names_from_catalog(_CATALOG) == {
        "schedule", "reminders", "system_monitor"}


def test_parse_skill_route():
    valid = {"schedule"}
    out = _parse_classification(
        '{"route": "skill:schedule", "confidence": 0.82, "target": "esta semana"}',
        valid)
    assert out == {
        "intent": "skill", "skill_name": "schedule",
        "_source": "capa_cloud_classifier", "confidence": 0.82,
        "target": "esta semana"}


def test_parse_rejects_unknown_skill():
    # El LLM inventó una skill que no existe → degradar a chat (None).
    out = _parse_classification(
        '{"route": "skill:teletransporte", "confidence": 0.9}', {"schedule"})
    assert out is None


def test_parse_chat_route_is_none():
    assert _parse_classification('{"route": "chat", "confidence": 0.4}', set()) is None


def test_parse_automation_route():
    out = _parse_classification(
        '{"route": "automation", "confidence": 0.7, "target": "discord"}', set())
    assert out["intent"] == "automation"
    assert out["_source"] == "capa_cloud_classifier"


def test_parse_strips_markdown_fences():
    raw = '```json\n{"route": "skill:reminders", "confidence": 0.6}\n```'
    out = _parse_classification(raw, {"reminders"})
    assert out is not None and out["skill_name"] == "reminders"


def test_parse_invalid_json_is_none():
    assert _parse_classification("no soy json {", {"schedule"}) is None


def test_parse_clamps_confidence():
    out = _parse_classification(
        '{"route": "skill:schedule", "confidence": 5}', {"schedule"})
    assert out["confidence"] == 1.0


# ── classify_intent (con LLM mockeado) ───────────────────────────────────────
@pytest.mark.asyncio
async def test_classify_routes_free_form_to_schedule(monkeypatch):
    _patch_llm_response(
        monkeypatch,
        '{"route": "skill:schedule", "confidence": 0.85, "target": "esta semana"}')
    out = await classify_intent(
        "fijate qué tengo encima esta semana", _fake_llm(), _CATALOG)
    assert out is not None
    assert out["intent"] == "skill" and out["skill_name"] == "schedule"


@pytest.mark.asyncio
async def test_classify_auto_discovers_new_skill(monkeypatch):
    # Skill nueva agregada SOLO al catálogo (sin tocar el classifier).
    catalog = _CATALOG + "\n- nutrition: Registro de comidas y macros"
    _patch_llm_response(
        monkeypatch, '{"route": "skill:nutrition", "confidence": 0.78}')
    out = await classify_intent("anotá que almorcé pollo con arroz", _fake_llm(), catalog)
    assert out is not None and out["skill_name"] == "nutrition"


@pytest.mark.asyncio
async def test_classify_chat_returns_none(monkeypatch):
    _patch_llm_response(monkeypatch, '{"route": "chat", "confidence": 0.3}')
    assert await classify_intent("hola cómo andás", _fake_llm(), _CATALOG) is None


@pytest.mark.asyncio
async def test_classify_empty_catalog_short_circuits(monkeypatch):
    # Sin catálogo no se gasta LLM.
    called = []
    async def fake_call(llm, prompt):
        called.append(1)
        return '{"route": "chat"}'
    monkeypatch.setattr(clf, "_call_extractor_llm", fake_call)
    assert await classify_intent("lo que sea", _fake_llm(), "") is None
    assert not called


@pytest.mark.asyncio
async def test_classify_no_llm_returns_none(monkeypatch):
    # llm_manager sin generate/_call_cloud → None sin romper.
    assert await classify_intent("x", object(), _CATALOG) is None


@pytest.mark.asyncio
async def test_classify_llm_exception_is_graceful(monkeypatch):
    async def boom(llm, prompt):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(clf, "_call_extractor_llm", boom)
    assert await classify_intent("fijate mi agenda", _fake_llm(), _CATALOG) is None


def test_classifier_source_confidence():
    from core.confidence import confidence_of
    analysis = {"intent": "skill", "skill_name": "schedule",
                "_source": "capa_cloud_classifier"}
    # Tiene confidence explícito en uso real; sin él, cae al _source = 0.7.
    assert confidence_of(analysis) == 0.7
