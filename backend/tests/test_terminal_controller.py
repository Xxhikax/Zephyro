"""Tests del TerminalController.

Estos tests son Windows-only (el controller solo soporta Windows por ahora)
y ejecutan PowerShell real en localhost. NO mockeamos subprocess porque la
integración con PowerShell real es lo que queremos validar.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

import pytest

from core.platform import IS_WINDOWS
from core.request_context import RequestContext
from modules.automation.terminal_controller import (
    TerminalController, TerminalResult,
)


pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="TerminalController es Windows-only")


# ── Fixtures ────────────────────────────────────────────────────────────

class _FakeApprovalGate:
    """Aprueba o rechaza según se configure."""
    def __init__(self, action: str = "view"):
        self.action = action
        self.calls = 0

    async def start(self, user_id: str) -> str:
        return f"req-{user_id}"

    async def send_approval_request(self, request_id: str, text: str) -> str:
        self.calls += 1
        return request_id

    async def wait_for_approval(self, request_id: str, timeout: float = 120.0) -> str:
        return self.action


@pytest.fixture
def ctx():
    return RequestContext(
        user_role="admin", user_name="test", authenticated=True,
        session_id="s1", request_id="rid-123", channel_type="websocket",
    )


@pytest.fixture
def ctx_telegram():
    return RequestContext(
        user_role="admin", user_name="test", authenticated=True,
        session_id="s1", request_id="rid-tg", channel_type="telegram",
    )


# ── READ: ejecuta sin aprobación ────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_command_runs_without_approval(ctx):
    ctrl = TerminalController(approval_gate=None)
    result = await ctrl.run("Get-Location", ctx)
    assert isinstance(result, TerminalResult)
    assert result.tier == "read"
    assert result.exit_code == 0
    assert result.approved is True
    assert result.ok


@pytest.mark.asyncio
async def test_read_captures_stdout(ctx):
    ctrl = TerminalController()
    result = await ctrl.run("Write-Output 'hola-test'", ctx)
    assert result.ok
    assert "hola-test" in result.stdout


# ── WRITE: requiere aprobación ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_blocked_without_gate(ctx):
    ctrl = TerminalController(approval_gate=None)
    result = await ctrl.run("New-Item -Path nonexistent.txt", ctx)
    assert result.tier == "write"
    assert result.error is not None
    assert result.approved is False


@pytest.mark.asyncio
async def test_write_blocked_when_gate_rejects(ctx, tmp_path):
    gate = _FakeApprovalGate(action="ignore")
    ctrl = TerminalController(approval_gate=gate)
    target = tmp_path / "should_not_exist.txt"
    result = await ctrl.run(f"New-Item -Path '{target}'", ctx)
    assert result.tier == "write"
    assert result.approved is False
    assert result.error is not None
    assert not target.exists()


@pytest.mark.asyncio
async def test_write_executes_when_approved(ctx, tmp_path):
    gate = _FakeApprovalGate(action="view")
    ctrl = TerminalController(approval_gate=gate)
    target = tmp_path / "approved.txt"
    result = await ctrl.run(f"New-Item -Path '{target}' -ItemType File", ctx)
    assert result.tier == "write"
    assert result.approved is True
    assert result.ok
    assert target.exists()
    assert gate.calls == 1


# ── DESTRUCTIVE: bloqueado sin flag ─────────────────────────────────────

@pytest.mark.asyncio
async def test_destructive_blocked_by_default(ctx):
    ctrl = TerminalController(approval_gate=_FakeApprovalGate("view"))
    result = await ctrl.run("Remove-Item -Recurse -Force C:\\nope_does_not_exist", ctx)
    assert result.tier == "destructive"
    assert result.approved is False
    assert "destructivo" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_destructive_runs_with_flag_and_approval(ctx, tmp_path):
    # Creamos un dir target real, luego lo borramos con el flag
    target = tmp_path / "to_delete"
    target.mkdir()
    (target / "f.txt").write_text("x")

    gate = _FakeApprovalGate(action="view")
    ctrl = TerminalController(approval_gate=gate)
    result = await ctrl.run(
        f"Remove-Item -Recurse -Force '{target}'", ctx,
        allow_destructive=True,
    )
    assert result.tier == "destructive"
    assert result.approved is True
    assert result.ok
    assert not target.exists()


# ── Channel guard ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_blocked_from_telegram(ctx_telegram):
    gate = _FakeApprovalGate("view")
    ctrl = TerminalController(approval_gate=gate)
    result = await ctrl.run("New-Item -Path foo.txt", ctx_telegram)
    assert result.error is not None
    assert "tauri" in result.error.lower() or "principal" in result.error.lower()
    assert gate.calls == 0


@pytest.mark.asyncio
async def test_read_allowed_from_telegram(ctx_telegram):
    ctrl = TerminalController(approval_gate=None)
    result = await ctrl.run("Get-Location", ctx_telegram)
    assert result.ok


# ── Timeout ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_kills_long_running(ctx):
    ctrl = TerminalController()
    result = await ctrl.run("Start-Sleep -Seconds 10", ctx, timeout=2.0)
    assert result.exit_code == -1
    assert "timeout" in (result.error or "").lower()
    assert result.duration_ms < 5000  # debería matar antes de 5s


# ── Output cap ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_large_output_truncated(ctx):
    ctrl = TerminalController()
    result = await ctrl.run("1..50000 | ForEach-Object { 'x' * 100 }", ctx, timeout=30.0)
    assert result.ok
    assert result.truncated is True
    assert len(result.stdout.encode("utf-8")) <= 100 * 1024 + 100


# ── Audit log ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_log_entry_written(ctx, tmp_path, monkeypatch):
    log_path = tmp_path / "terminal.log"
    ctrl = TerminalController()
    monkeypatch.setattr(ctrl, "_log_path", log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    await ctrl.run("Get-Location", ctx)

    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "rid-123" in content
    assert "tier=read" in content
    assert "Get-Location" in content
