# -*- coding: utf-8 -*-
"""
Escalado por confianza en cloud mode (Fase 1).

Cuando la heurística NO resuelve en cloud mode, se reactiva el extractor LLM
(extract_browser_intent) para entender formas libres ("che tirame el discord")
en vez de caer al chat alucinado. El LLM se mockea (se inyecta como parámetro /
se monkeypatchea el import) — no se llama API real.

Invariante de costo clave: el escalado solo ocurre cuando el mensaje menciona un
target accionable conocido. Charla pura NO gasta una llamada LLM.
"""
import types
import pytest

from core.orchestrator import Orchestrator
import core.orchestrator as orch_mod


def _orch():
    fake = types.SimpleNamespace()
    fake.llm = object()  # dummy; el extractor está mockeado
    fake.console = types.SimpleNamespace(print=lambda *a, **k: None)
    fake._cloud_llm_escalate = types.MethodType(Orchestrator._cloud_llm_escalate, fake)
    return fake


def _automation_analysis(target="discord", ai="NAVIGATE"):
    return {"intent": "automation", "automation_intent": ai, "target": target,
            "_source": "capa1.5_llm_extractor"}


@pytest.mark.asyncio
@pytest.mark.parametrize("text,target", [
    ("che tirame el discord", "discord"),
    ("necesito spotify ya", "spotify"),
    ("fijate youtube un toque", "youtube"),
    ("poneme algo de lofi en youtube music", "youtube music"),
])
async def test_escalates_free_form_with_known_target(monkeypatch, text, target):
    calls = []
    async def fake_extract(ui, llm):
        calls.append(ui)
        return _automation_analysis(target=target)
    monkeypatch.setattr(orch_mod, "extract_browser_intent", fake_extract)

    o = _orch()
    result = await o._cloud_llm_escalate(text)
    assert result is not None, f"{text!r} debería escalar (menciona {target})"
    assert result["intent"] == "automation"
    assert result["complexity"] == "complex"        # enriquecido
    assert len(calls) == 1, "debió llamar al extractor LLM una vez"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "hola cómo va todo",
    "contame un chiste",
    "qué aburrido está el día",
    "la verdad no sé qué hacer hoy",
])
async def test_pure_chat_does_not_call_llm(monkeypatch, text):
    # Guard de costo: sin target conocido → NO se llama al LLM (fast lane).
    calls = []
    async def fake_extract(ui, llm):
        calls.append(ui)
        return None
    monkeypatch.setattr(orch_mod, "extract_browser_intent", fake_extract)

    o = _orch()
    result = await o._cloud_llm_escalate(text)
    assert result is None
    assert len(calls) == 0, f"charla pura NO debe gastar LLM: {text!r}"


@pytest.mark.asyncio
async def test_extractor_returns_none_falls_through(monkeypatch):
    # Menciona target conocido pero el LLM decide que no es comando → None.
    async def fake_extract(ui, llm):
        return None
    monkeypatch.setattr(orch_mod, "extract_browser_intent", fake_extract)
    o = _orch()
    assert await o._cloud_llm_escalate("estaba pensando en discord ayer") is None


@pytest.mark.asyncio
async def test_extractor_exception_is_graceful(monkeypatch):
    async def boom(ui, llm):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(orch_mod, "extract_browser_intent", boom)
    o = _orch()
    # No debe romper; devuelve None → el caller cae al fast lane.
    assert await o._cloud_llm_escalate("abrime el discord") is None


@pytest.mark.asyncio
async def test_extractor_non_automation_ignored(monkeypatch):
    async def fake_extract(ui, llm):
        return {"intent": "information", "_source": "capa1.5_llm_extractor"}
    monkeypatch.setattr(orch_mod, "extract_browser_intent", fake_extract)
    o = _orch()
    assert await o._cloud_llm_escalate("contame de discord") is None


def test_escalated_analysis_confidence():
    # El analysis del extractor debe mapear a 0.7 en confidence_of (capa1.5).
    from core.confidence import confidence_of
    assert confidence_of(_automation_analysis()) == 0.7


# ── Rama B — classifier registry-aware (Fase 2) ──────────────────────────────
def _orch_with_registry(skills=("schedule",), enabled=True):
    """Fake orchestrator con skill_registry para ejercitar la rama B."""
    o = _orch()
    _skills = {name: types.SimpleNamespace(name=name, enabled=enabled)
               for name in skills}
    o.skill_registry = types.SimpleNamespace(
        get_prompt_summaries=lambda: "\n".join(
            f"- {n}: desc de {n}" for n in _skills),
        get_skill=lambda n: _skills.get(n),
    )
    return o


@pytest.mark.asyncio
async def test_rama_b_routes_to_skill(monkeypatch):
    # Forma libre sin app conocida → rama A no aplica → classifier rutea a skill.
    async def no_browser(ui, llm):
        return None
    monkeypatch.setattr(orch_mod, "extract_browser_intent", no_browser)

    async def fake_classify(ui, llm, catalog):
        return {"intent": "skill", "skill_name": "schedule",
                "_source": "capa_cloud_classifier", "confidence": 0.8}
    monkeypatch.setattr(orch_mod, "classify_intent", fake_classify)

    o = _orch_with_registry()
    result = await o._cloud_llm_escalate("fijate qué tengo encima esta semana")
    assert result is not None
    assert result["intent"] == "skill"
    assert result["skill_name"] == "schedule"
    assert result["_source"] == "capa_cloud_classifier"


@pytest.mark.asyncio
async def test_rama_a_short_circuits_before_classifier(monkeypatch):
    # "abrí discord" menciona target conocido → rama A resuelve, classifier NO corre.
    async def fake_extract(ui, llm):
        return _automation_analysis(target="discord")
    monkeypatch.setattr(orch_mod, "extract_browser_intent", fake_extract)

    classifier_calls = []
    async def fake_classify(ui, llm, catalog):
        classifier_calls.append(ui)
        return {"intent": "skill", "skill_name": "schedule"}
    monkeypatch.setattr(orch_mod, "classify_intent", fake_classify)

    o = _orch_with_registry()
    result = await o._cloud_llm_escalate("abrí discord")
    assert result["intent"] == "automation"
    assert not classifier_calls, "rama A resolvió; el classifier no debe correr"


@pytest.mark.asyncio
async def test_rama_b_unknown_skill_returns_none(monkeypatch):
    async def no_browser(ui, llm):
        return None
    monkeypatch.setattr(orch_mod, "extract_browser_intent", no_browser)

    async def fake_classify(ui, llm, catalog):
        return {"intent": "skill", "skill_name": "no_existe",
                "_source": "capa_cloud_classifier"}
    monkeypatch.setattr(orch_mod, "classify_intent", fake_classify)

    o = _orch_with_registry(skills=("schedule",))
    assert await o._cloud_llm_escalate("hacé magia rara") is None


@pytest.mark.asyncio
async def test_rama_b_disabled_skill_returns_none(monkeypatch):
    async def no_browser(ui, llm):
        return None
    monkeypatch.setattr(orch_mod, "extract_browser_intent", no_browser)

    async def fake_classify(ui, llm, catalog):
        return {"intent": "skill", "skill_name": "schedule",
                "_source": "capa_cloud_classifier"}
    monkeypatch.setattr(orch_mod, "classify_intent", fake_classify)

    o = _orch_with_registry(skills=("schedule",), enabled=False)
    assert await o._cloud_llm_escalate("fijate mi agenda") is None


@pytest.mark.asyncio
async def test_rama_b_chat_returns_none(monkeypatch):
    async def no_browser(ui, llm):
        return None
    monkeypatch.setattr(orch_mod, "extract_browser_intent", no_browser)

    async def fake_classify(ui, llm, catalog):
        return None  # route=chat
    monkeypatch.setattr(orch_mod, "classify_intent", fake_classify)

    o = _orch_with_registry()
    assert await o._cloud_llm_escalate("qué lindo día hace hoy") is None


@pytest.mark.asyncio
async def test_rama_b_classifier_exception_is_graceful(monkeypatch):
    async def no_browser(ui, llm):
        return None
    monkeypatch.setattr(orch_mod, "extract_browser_intent", no_browser)

    async def boom(ui, llm, catalog):
        raise RuntimeError("classifier down")
    monkeypatch.setattr(orch_mod, "classify_intent", boom)

    o = _orch_with_registry()
    assert await o._cloud_llm_escalate("fijate mi agenda esta semana") is None
