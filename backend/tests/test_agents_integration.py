"""
Tests de integración Phase 4 — agent system + VerificationAgent.

Cubre:
  - Import + instanciación de todos los agentes
  - VerificationAgent: reglas deterministas (sin LLM)
  - VerificationAgent: pipeline completo con conn_registry mock
  - OrchestratorAgent.classify() para los 4 dominios
  - Event bus → VerificationAgent.on_skill_executed flow
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Phase 4a: VerificationAgent ─────────────────────────────────────────────

@pytest.fixture
def mock_conn_registry():
    reg = MagicMock()
    reg.broadcast = AsyncMock(return_value=1)
    return reg


@pytest.fixture
def verification_agent(mock_conn_registry, tmp_path):
    from agents.verification_agent import VerificationAgent
    return VerificationAgent(
        conn_registry=mock_conn_registry,
        code_root=str(tmp_path),
        audit_path=str(tmp_path / "audit.jsonl"),
    )


@pytest.mark.asyncio
async def test_verify_terminal_extracts_exit_zero(verification_agent):
    """Terminal con exit 0 → ok=True."""
    event = {
        "skill_name": "terminal",
        "input_preview": "ejecuta dir",
        "response": "**WIN** · exit 0 · 120ms\n\n```\nC:\\\n```",
    }
    result = await verification_agent._verify_terminal(event)
    assert result["ok"] is True
    assert "exit_code=0" in result["evidence"]


@pytest.mark.asyncio
async def test_verify_terminal_extracts_exit_nonzero(verification_agent):
    """Terminal con exit != 0 → ok=False."""
    event = {
        "skill_name": "terminal",
        "input_preview": "ejecuta blah",
        "response": "**WIN** · exit 1 · 80ms\n\n_stderr:_\n```\ncommand not found\n```",
    }
    result = await verification_agent._verify_terminal(event)
    assert result["ok"] is False
    assert "exit_code=1" in result["evidence"]


@pytest.mark.asyncio
async def test_verify_terminal_no_exit_code(verification_agent):
    """Sin exit code en respuesta → ok=None (inconclusivo)."""
    event = {"skill_name": "terminal", "response": "comando ejecutado"}
    result = await verification_agent._verify_terminal(event)
    assert result["ok"] is None


@pytest.mark.asyncio
async def test_verify_file_existing(verification_agent, tmp_path):
    """File-ops mencionando archivo existente → ok=True."""
    target = tmp_path / "test_artifact.txt"
    target.write_text("hello", encoding="utf-8")
    event = {
        "skill_name": "file-ops",
        "input_preview": "crea test_artifact.txt",
        "response": f"Archivo `{target.name}` creado",
    }
    result = await verification_agent._verify_file(event)
    assert result["ok"] is True
    assert "exists" in result["evidence"]


@pytest.mark.asyncio
async def test_verify_file_missing(verification_agent):
    """File-ops mencionando archivo inexistente → ok=False."""
    event = {
        "skill_name": "file-ops",
        "input_preview": "leer fakefile.xyz",
        "response": "Procesé `nonexistent_qwerty.txt`",
    }
    result = await verification_agent._verify_file(event)
    assert result["ok"] is False
    assert "NOT FOUND" in result["evidence"]


@pytest.mark.asyncio
async def test_verify_storage_clean(verification_agent):
    """Schedule/reminders sin errores → ok=True."""
    event = {
        "skill_name": "schedule",
        "response": "Evento creado para mañana 15:00",
    }
    result = await verification_agent._verify_storage(event)
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_verify_storage_error(verification_agent):
    """Schedule/reminders con error → ok=False."""
    event = {
        "skill_name": "reminders",
        "response": "Error: no pude crear el recordatorio",
    }
    result = await verification_agent._verify_storage(event)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_pipeline_writes_audit_log(verification_agent, mock_conn_registry, tmp_path):
    """Pipeline completo escribe al audit log y notifica clientes."""
    # Skill no en LLM_CHECK_SKILLS para evitar llamada a DeepSeek
    event = {
        "skill_name": "reminders",
        "input_preview": "recuérdame comprar leche",
        "response": "Recordatorio creado para mañana",
    }
    await verification_agent._verify_async(event)

    # Broadcast llamado con type=verification
    mock_conn_registry.broadcast.assert_awaited_once()
    payload = mock_conn_registry.broadcast.await_args[0][0]
    assert payload["type"] == "verification"
    assert payload["skill"] == "reminders"

    # Audit log escrito
    audit = tmp_path / "audit.jsonl"
    assert audit.exists()
    entry = json.loads(audit.read_text(encoding="utf-8").strip())
    assert entry["skill"] == "reminders"
    assert "recuérdame" in entry["input"] or "recu" in entry["input"]


@pytest.mark.asyncio
async def test_on_skill_executed_fire_and_forget(verification_agent):
    """on_skill_executed lanza task pero no bloquea."""
    event = {"skill_name": "reminders", "response": "ok"}
    await verification_agent.on_skill_executed(event)
    # Yield el control para que la task interna empiece
    await asyncio.sleep(0.05)


# ── Phase 4b/4c: OrchestratorAgent classification ───────────────────────────

@pytest.fixture
def orchestrator_agent():
    """Construye OrchestratorAgent con un fake orchestrator."""
    from agents.orchestrator_agent import OrchestratorAgent

    fake_registry = MagicMock()
    fake_registry.get_skill = MagicMock(return_value=None)
    fake_orch = MagicMock()
    fake_orch.skill_registry = fake_registry
    fake_orch.handle_input = AsyncMock(return_value="fallback")

    return OrchestratorAgent(fake_orch)


def test_classify_dev(orchestrator_agent):
    assert orchestrator_agent.classify("haz un git commit") == "dev"
    assert orchestrator_agent.classify("explica esta función de python") == "dev"


def test_classify_browser(orchestrator_agent):
    assert orchestrator_agent.classify("abre youtube") == "browser"
    assert orchestrator_agent.classify("busca recetas en google") in ("browser", "dev")  # ambiguous


def test_classify_system(orchestrator_agent):
    assert orchestrator_agent.classify("cuánta RAM tengo libre") == "system"
    assert orchestrator_agent.classify("ejecuta el comando dir") in ("system", "dev")


def test_classify_memory(orchestrator_agent):
    assert orchestrator_agent.classify("recuérdame comprar pan") == "memory"
    assert orchestrator_agent.classify("qué tengo en la agenda hoy") == "memory"


def test_classify_no_match(orchestrator_agent):
    """Frases sin keywords → None (delega al orch base)."""
    assert orchestrator_agent.classify("hola, cómo estás") is None


def test_orchestrator_has_all_agents(orchestrator_agent):
    agents = orchestrator_agent.get_agents()
    assert set(agents.keys()) == {"dev", "browser", "system", "memory"}


@pytest.mark.asyncio
async def test_orchestrator_falls_back_when_agent_returns_empty(orchestrator_agent):
    """Si el agente especializado devuelve AgentResult vacío, va al orch base."""
    from core.request_context import RequestContext
    ctx = RequestContext(
        user_role="admin", user_name="test", authenticated=True, session_id="t1",
    )
    # "hola" no matchea ningún keyword → directo al fallback
    result = await orchestrator_agent.handle("hola", ctx)
    assert result.skill_used == "orchestrator_base"
    assert result.response == "fallback"


# ── Imports completos (smoke test) ──────────────────────────────────────────

def test_all_agent_modules_importable():
    """Todos los módulos de agents/ importan sin error."""
    import agents
    import agents.base
    import agents.verification_agent
    import agents.orchestrator_agent
    import agents.dev_agent
    import agents.browser_agent
    import agents.system_agent
    import agents.memory_agent
    assert agents.base.BaseAgent is not None
    assert agents.base.AgentResult is not None


def test_routers_agents_importable():
    """El router REST /api/agents importa sin error."""
    from routers import agents as agents_router
    assert agents_router.router is not None
    assert agents_router.router.prefix == "/agents"
