# -*- coding: utf-8 -*-
"""Regresión: el diagnóstico siempre cierra el panel, y no mira un log de ruido.

Dos bugs reales (2026-09-29), encontrados con el panel colgado en
`RUNNING · analizando… (0 fases)`:

- **Cierre**: `DiagnosticAgent.run()` tenía early returns ("no hay líneas de log",
  "no hay errores") que devolvían un string sin emitir evento terminal. El panel
  (`src/diagnostic.js`) cierra con `diag:resolved`/`diag:expired`, así que quedaba
  colgado para siempre. El chat sí cerraba —va por `diagnostic_running/done`—, lo
  que disimulaba el bug.
- **Ceguera**: la ventana de 50 líneas estaba inundada por el poller de Telegram
  (36 de las últimas 50 líneas medidas). "No encontré errores" era un falso
  negativo estructural.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subagents.diagnostic_agent import DiagnosticAgent, _NOISE_RE  # noqa: E402


class FakeStore:
    def __init__(self, status="running"):
        self.state = {"request_id": "r1", "user_id": "u1", "status": status,
                      "progress_log": [], "action": None}

    def load(self, request_id):
        return dict(self.state)

    def update(self, request_id, **kwargs):
        self.state.update(kwargs)

    def append_progress(self, request_id, text):
        self.state["progress_log"].append(text)

    def save(self, request_id, state):
        self.state = state


class FakeRegistry:
    def __init__(self):
        self.pushed: list[dict] = []

    async def push(self, user_id, payload):
        self.pushed.append(payload)

    async def broadcast(self, payload):
        self.pushed.append(payload)


def _gate(status="running"):
    from subagents.ws_approval_gate import WebSocketApprovalGate
    store = FakeStore(status)
    registry = FakeRegistry()
    gate = WebSocketApprovalGate(connection_registry=registry, store=store)
    return gate, store, registry


def _agent(gate, log_file, **kw):
    kw.setdefault("code_root", os.path.dirname(str(log_file)))
    return DiagnosticAgent(llm_manager=None, gate=gate, log_file=str(log_file), **kw)


def _pushed(registry, type_):
    return [p for p in registry.pushed if p.get("type") == type_]


# ── gate.send_resolved ───────────────────────────────────────────────────────

async def test_send_resolved_pushes_terminal_event():
    gate, store, registry = _gate()
    assert await gate.send_resolved("r1", summary="todo ok") is True
    ev = _pushed(registry, "diag:resolved")
    assert len(ev) == 1
    assert ev[0]["action"] == "completed"
    assert ev[0]["summary"] == "todo ok"
    assert store.state["status"] == "resolved"


@pytest.mark.parametrize("status", ["resolved", "expired"])
async def test_send_resolved_does_not_double_close(status):
    """El camino de aprobación ya cierra el run (routers/chat.py)."""
    gate, _store, registry = _gate(status)
    assert await gate.send_resolved("r1") is False
    assert _pushed(registry, "diag:resolved") == []


# ── run() cierra siempre ─────────────────────────────────────────────────────

async def test_run_closes_the_panel_when_there_are_no_errors(tmp_path):
    """El caso exacto del bug: log limpio → antes se colgaba."""
    log = tmp_path / "backend.log"
    log.write_text(
        "10:00:00 [INFO] zephyro.web: todo bien (web_app.py:1)\n" * 5,
        encoding="utf-8",
    )
    gate, store, registry = _gate()
    result = await _agent(gate, log).run(chat_id="r1", n_lines=50)

    assert "No se encontraron errores" in result
    assert len(_pushed(registry, "diag:resolved")) == 1
    assert store.state["status"] == "resolved"


async def test_run_closes_the_panel_when_the_log_file_is_missing(tmp_path):
    gate, _store, registry = _gate()
    result = await _agent(gate, tmp_path / "no-existe.log").run(chat_id="r1")
    assert "No se encontraron líneas de log" in result
    assert len(_pushed(registry, "diag:resolved")) == 1


async def test_run_closes_the_panel_even_when_a_phase_explodes(tmp_path, monkeypatch):
    log = tmp_path / "backend.log"
    log.write_text("10:00:00 [ERROR] zephyro.web: boom (web_app.py:1)\n", encoding="utf-8")
    gate, _store, registry = _gate()
    agent = _agent(gate, log)

    def boom(*a, **kw):
        raise RuntimeError("fase rota")

    monkeypatch.setattr(agent, "_resolve_code_context", boom)

    with pytest.raises(RuntimeError):
        await agent.run(chat_id="r1", n_lines=50)
    assert len(_pushed(registry, "diag:resolved")) == 1


async def test_run_does_not_emit_twice_if_already_resolved(tmp_path):
    log = tmp_path / "backend.log"
    log.write_text("10:00:00 [INFO] zephyro.web: ok (web_app.py:1)\n", encoding="utf-8")
    gate, _store, registry = _gate(status="resolved")
    await _agent(gate, log).run(chat_id="r1", n_lines=50)
    assert _pushed(registry, "diag:resolved") == []


# ── Filtro de ruido ──────────────────────────────────────────────────────────

_NOISE_LINE = (
    "10:00:00 [INFO] httpx: HTTP Request: POST "
    "https://api.telegram.org/botXXX/getUpdates \"HTTP/1.1 200 OK\" (_client.py:1740)"
)


def test_noise_regex_matches_polling_but_not_real_logs():
    assert _NOISE_RE.search(_NOISE_LINE)
    assert not _NOISE_RE.search("10:00:00 [ERROR] zephyro.web: boom (web_app.py:1)")
    assert not _NOISE_RE.search(
        "10:00:00 [WARNING] httpx: HTTP Request: POST https://api.anthropic.com/v1/messages"
    )


def test_tail_skips_polling_noise(tmp_path):
    """40 líneas de ruido tapaban 3 warnings reales: ese era el falso negativo."""
    log = tmp_path / "backend.log"
    lines = [f"09:0{i % 10}:00 [WARNING] zephyro.x: warn-{i} (x.py:{i})" for i in range(3)]
    lines += [_NOISE_LINE] * 40
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    gate, _store, _registry = _gate()
    tail = _agent(gate, log)._tail_log_file(10)

    assert all(not _NOISE_RE.search(line) for line in tail)
    assert sum("warn-" in line for line in tail) == 3


def test_tail_keeps_traceback_lines(tmp_path):
    """El filtro es angosto: las líneas de traceback no matchean _LOG_RE y
    igual tienen que sobrevivir, o _enrich_with_tracebacks se queda sin nada."""
    log = tmp_path / "backend.log"
    log.write_text(
        "10:00:00 [ERROR] zephyro.web: boom (web_app.py:1)\n"
        "Traceback (most recent call last):\n"
        '  File "web_app.py", line 42, in handler\n'
        "    raise ValueError()\n",
        encoding="utf-8",
    )
    gate, _store, _registry = _gate()
    tail = _agent(gate, log)._tail_log_file(50)
    assert any("Traceback" in line for line in tail)
    assert any('File "web_app.py"' in line for line in tail)
