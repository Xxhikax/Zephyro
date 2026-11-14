"""Tests para core.task_decomposer — split heurístico de instrucciones."""
import pytest

from core.task_decomposer import decompose, needs_llm_fallback, llm_decompose


def test_single_task_unchanged():
    assert decompose("agenda cálculo el lunes a las 7") == ["agenda cálculo el lunes a las 7"]


def test_cross_domain_splits_into_two():
    tasks = decompose("agendá cálculo el lunes y después poné música en yt music")
    assert len(tasks) == 2
    assert "agendá cálculo" in tasks[0].lower()
    assert "música" in tasks[1].lower()


def test_false_y_same_object_not_split():
    # "música tranquila y relajante" → una sola acción (segundo lado sin verbo)
    assert decompose("ponme música tranquila y relajante") == ["ponme música tranquila y relajante"]


def test_two_objects_one_verb_not_split():
    # "cálculo y álgebra el lunes" → un solo evento (segundo lado sin verbo)
    assert decompose("agendá cálculo y álgebra el lunes") == ["agendá cálculo y álgebra el lunes"]


def test_empty_input():
    assert decompose("") == [""]


def test_info_request_with_two_verbs_still_splits_syntactically():
    """Documenta el comportamiento: «busca … y analiza …» SÍ se parte acá porque
    ambos segmentos tienen verbo (search + misc). Que ninguno sea un comando
    ejecutable lo decide el orchestrator (_run_multitask → None → flujo single),
    no el decomposer. Ver test_multitask_orchestration."""
    tasks = decompose("busca informacion sobre el modelo fable 5.1 y analiza el benchmark de este")
    assert tasks == [
        "busca informacion sobre el modelo fable 5.1",
        "analiza el benchmark de este",
    ]


def test_strong_connector_both_verbs():
    tasks = decompose("abre firefox y luego reproduce lofi")
    assert len(tasks) == 2


def test_needs_llm_fallback_strong_connector_unclear():
    # Conector secuencial explícito pero segundo lado sin verbo claro →
    # candidato a desambiguación LLM.
    assert needs_llm_fallback("agendá cálculo y después eso otro") is True


def test_no_llm_fallback_without_strong_connector():
    assert needs_llm_fallback("ponme música tranquila y relajante") is False


@pytest.mark.asyncio
async def test_llm_decompose_parses_json():
    async def fake_llm(prompt):
        return '{"tasks": ["agendá cálculo", "poné música"]}'
    tasks = await llm_decompose("agendá cálculo y poné música", fake_llm)
    assert tasks == ["agendá cálculo", "poné música"]


@pytest.mark.asyncio
async def test_llm_decompose_falls_back_on_bad_json():
    async def bad_llm(prompt):
        return "no soy json"
    tasks = await llm_decompose("algo raro", bad_llm)
    assert tasks == ["algo raro"]


@pytest.mark.asyncio
async def test_llm_decompose_handles_caller_error():
    async def boom(prompt):
        raise RuntimeError("llm down")
    tasks = await llm_decompose("agendá X", boom)
    assert tasks == ["agendá X"]
