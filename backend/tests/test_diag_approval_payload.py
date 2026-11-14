#!/usr/bin/env python3
"""
Contrato de los payloads WS del approval gate que consume el aviso HABLADO de
call mode (src/call-mode.js `_announceDiagPending`).

Sin estos campos el aviso en manos libres no puede ser concreto: el usuario
escuchaba un chime y el `model_choice` se auto-resolvía en silencio al vencer
su timeout de 120 s.

Sin red: connection_registry y DiagnosticStore mockeados.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock

import pytest

from subagents.diagnostic_store import DiagnosticStore
from subagents.ws_approval_gate import WebSocketApprovalGate

OPTIONS = [
    {"id": "deepseek-flash", "label": "DeepSeek Flash (rápido)"},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5 (preciso)"},
]


def _make_gate(prefix: str = "diag", user_id: str = "admin"):
    registry = MagicMock()
    registry.push = AsyncMock(return_value=True)
    registry.broadcast = AsyncMock(return_value=1)
    store = MagicMock(spec=DiagnosticStore)
    store.load = MagicMock(return_value={"request_id": "abc", "user_id": user_id})
    store.save = MagicMock()
    store.update = MagicMock()
    store.append_progress = MagicMock()
    return WebSocketApprovalGate(registry, store, event_prefix=prefix), registry


def _pushed(registry, msg_type: str) -> dict:
    """Último payload empujado con ese `type` (o {} si no hubo)."""
    for call in reversed(registry.push.call_args_list):
        payload = call[0][1]
        if payload.get("type") == msg_type:
            return payload
    return {}


# ── model_choice: timeout_sec + default_choice ──────────────────────────────

@pytest.mark.asyncio
async def test_model_choice_payload_carries_timeout_and_default():
    gate, registry = _make_gate()
    # timeout corto: no queremos esperar 120 s en el test.
    choice = await gate.request_model_choice(
        "admin", "browser_no_abre", OPTIONS, timeout=0.05,
    )
    payload = _pushed(registry, "diag:model_choice")
    assert payload, "no se empujó diag:model_choice"
    assert payload["timeout_sec"] == 0.05
    assert payload["default_choice"] == "deepseek-flash"  # options[0].id
    assert payload["options"] == OPTIONS
    # Sin respuesta → el gate elige el default (comportamiento preexistente).
    assert choice == "deepseek-flash"


@pytest.mark.asyncio
async def test_model_choice_timeout_announces_auto_action():
    """diag:expired debe decir QUÉ eligió solo, no solo que expiró."""
    gate, registry = _make_gate()
    await gate.request_model_choice("admin", "x", OPTIONS, timeout=0.05)
    expired = _pushed(registry, "diag:expired")
    assert expired, "no se empujó diag:expired"
    assert expired["reason"] == "timeout"
    assert expired["auto_action"] == "deepseek-flash"


@pytest.mark.asyncio
async def test_model_choice_without_options_defaults_to_ignore():
    gate, registry = _make_gate()
    choice = await gate.request_model_choice("admin", "x", [], timeout=0.05)
    payload = _pushed(registry, "diag:model_choice")
    assert payload["default_choice"] == "ignore"
    assert choice == "ignore"


@pytest.mark.asyncio
async def test_user_answer_wins_over_default():
    """Con respuesta del usuario no se usa el default ni se emite expired."""
    gate, registry = _make_gate()
    gate._store.load = MagicMock(
        return_value={"request_id": "abc", "user_id": "admin", "action": "claude-sonnet-5"}
    )

    async def _answer():
        await asyncio.sleep(0.01)
        # request_id lo genera el gate; en este punto hay exactamente uno pendiente.
        rid = next(iter(gate._pending))
        gate.resolve(rid, "claude-sonnet-5")

    task = asyncio.create_task(_answer())
    choice = await gate.request_model_choice("admin", "x", OPTIONS, timeout=5.0)
    await task
    assert choice == "claude-sonnet-5"
    assert not _pushed(registry, "diag:expired")


# ── approval: contrato SIN tocar ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approval_payload_shape_unchanged():
    """`send_approval_request` tiene 4 implementaciones (WS, Telegram x2, ABC):
    agregarle timeout_sec rompería el contrato, así que NO debe aparecer."""
    gate, registry = _make_gate()
    await gate.send_approval_request("abc", "hipótesis de prueba")
    payload = _pushed(registry, "diag:approval")
    assert payload["hypothesis_summary"] == "hipótesis de prueba"
    assert "timeout_sec" not in payload


# ── prefix terminal: el mismo gate sirve a TerminalController ───────────────

@pytest.mark.asyncio
async def test_terminal_prefix_still_works():
    gate, registry = _make_gate(prefix="terminal")
    await gate.send_approval_request("abc", "Get-Process")
    assert _pushed(registry, "terminal:approval")
    await gate.request_model_choice("admin", "x", OPTIONS, timeout=0.05)
    assert _pushed(registry, "terminal:model_choice")["default_choice"] == "deepseek-flash"
    assert _pushed(registry, "terminal:expired")["reason"] == "timeout"
