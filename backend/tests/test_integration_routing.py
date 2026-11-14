"""
Suite 3 — Integration routing tests.

Tests con skill real (SystemMonitorSkill) pero sin orchestrator completo.
Validan que el pipeline skill_registry.try_handle → resultado correcto funciona.

Tests cubiertos:
1. /ram → SystemMonitorSkill intercepts → respuesta contiene info de RAM
2. /red y /ram simultáneos → sin race condition
3. /noexiste → skill_registry.try_handle retorna None → fallback a LLM (mocked)
"""
import sys
import os
import pytest
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_request_context():
    """RequestContext mínimo para tests."""
    from core.request_context import RequestContext
    return RequestContext(
        user_role="admin",
        user_name=None,
        authenticated=True,
        session_id="test-session",
        channel_type="test",
        channel_user_id="test:1",
    )


async def load_skill_registry():
    """Carga un SkillRegistry real con SystemMonitorSkill."""
    from pathlib import Path
    from skills.registry import SkillRegistry
    from skills.loader import SkillLoader

    base = Path(__file__).parent.parent / "skills"
    skill_dirs = [base / "builtin", base / "community"]
    registry = SkillRegistry()
    loader = SkillLoader(skill_dirs)
    loader.load_into(registry)
    return registry


# ---------------------------------------------------------------------------
# Test 1 — /ram → SystemMonitorSkill → respuesta con info RAM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ram_pipeline_end_to_end():
    """
    /ram debe ser interceptado por SystemMonitorSkill vía try_handle
    y retornar información de RAM (no None, no ir al LLM).
    """
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil no disponible")

    registry = await load_skill_registry()
    ctx = make_request_context()

    result = await registry.try_handle("/ram", ctx)

    assert result is not None, (
        "/ram no fue interceptado por ninguna skill. "
        "¿SystemMonitorSkill está cargada y habilitada?"
    )
    # La respuesta debe mencionar algo sobre RAM/memoria
    result_lower = result.lower()
    assert any(kw in result_lower for kw in ("ram", "memoria", "mb", "gb", "%")), (
        f"La respuesta de /ram no parece ser sobre RAM: {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Dos comandos simultáneos → sin race condition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_commands_no_race():
    """
    /red y /ram ejecutados simultáneamente no deben causar race conditions.
    Ambos deben recibir respuestas válidas.
    """
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil no disponible")

    registry = await load_skill_registry()
    ctx = make_request_context()

    # Ejecutar simultáneamente
    results = await asyncio.gather(
        registry.try_handle("/ram", ctx),
        registry.try_handle("/red", ctx),
    )

    ram_result, red_result = results

    assert ram_result is not None, "/ram retornó None"
    assert red_result is not None, "/red retornó None"

    # Verificar que las respuestas son del tipo correcto
    assert isinstance(ram_result, str), f"Resultado /ram no es str: {type(ram_result)}"
    assert isinstance(red_result, str), f"Resultado /red no es str: {type(red_result)}"

    # No deben ser la misma respuesta (serían de skills distintas)
    # Solo verificamos que no hay error obvio
    for r in (ram_result, red_result):
        assert "error" not in r.lower() or len(r) > 20, (
            f"Respuesta parece ser un error: {r!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Comando inexistente → try_handle retorna None → fallback LLM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nonexistent_command_fallback_to_llm():
    """
    Un comando /noexistente debe ser ignorado por el skill_registry
    (try_handle devuelve None) y el flujo debe continuar al LLM mocked
    sin crash.
    """
    registry = await load_skill_registry()
    ctx = make_request_context()

    # try_handle debe retornar None para comandos no registrados
    result = await registry.try_handle("/comandoquenuncaexistira_xyz987", ctx)

    assert result is None, (
        f"Se esperaba None para comando inexistente, se obtuvo: {result!r}"
    )

    # Simular que el orchestrator hace fallback al LLM cuando result is None
    llm_response = "No reconozco ese comando, ¿en qué puedo ayudarte?"
    final_response = result if result is not None else llm_response

    assert final_response == llm_response
    assert "Traceback" not in final_response


# ---------------------------------------------------------------------------
# Test 4 — /cpu → SystemMonitorSkill → respuesta con info CPU
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cpu_pipeline_end_to_end():
    """
    /cpu debe ser interceptado y retornar información de CPU.
    """
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil no disponible")

    registry = await load_skill_registry()
    ctx = make_request_context()

    result = await registry.try_handle("/cpu", ctx)

    assert result is not None, "/cpu no fue interceptado por ninguna skill"
    result_lower = result.lower()
    assert any(kw in result_lower for kw in ("cpu", "procesador", "%", "núcleo", "nucleo", "core")), (
        f"La respuesta de /cpu no parece ser sobre CPU: {result!r}"
    )
