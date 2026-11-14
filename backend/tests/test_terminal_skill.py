"""Tests de integración de TerminalSkill y LogReaderSkill."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from core.platform import IS_WINDOWS
from core.request_context import RequestContext
from modules.automation.terminal_controller import (
    TerminalController, set_terminal_controller,
)
from skills.base import SkillMetadata
from skills.builtin.terminal.skill import TerminalSkill
from skills.builtin.log_reader.skill import LogReaderSkill


pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only")


@pytest.fixture
def ctx():
    return RequestContext(
        user_role="admin", user_name="test", authenticated=True,
        session_id="s1", request_id="rid-skill-test", channel_type="websocket",
    )


@pytest.fixture
def terminal_skill(tmp_path):
    md = SkillMetadata(
        name="terminal", description="t", triggers=["/run", "/exec", "/pwsh"],
    )
    return TerminalSkill(metadata=md, skill_dir=tmp_path)


@pytest.fixture
def log_skill(tmp_path):
    md = SkillMetadata(name="log-reader", description="l", triggers=["/logs"])
    return LogReaderSkill(metadata=md, skill_dir=tmp_path)


@pytest.fixture
def controller():
    c = TerminalController(approval_gate=None)
    set_terminal_controller(c)
    yield c
    set_terminal_controller(None)  # type: ignore[arg-type]


# ── TerminalSkill matching ──────────────────────────────────────────────

def test_matches_slash_run(terminal_skill):
    assert terminal_skill.matches_command("/run Get-Process")
    assert terminal_skill.matches_command("/exec ls")
    assert terminal_skill.matches_command("/pwsh git status")


def test_matches_natural_prefix_with_cmdlet(terminal_skill):
    assert terminal_skill.matches_command("ejecuta el comando Get-Process")
    assert terminal_skill.matches_command("corre el comando git status")
    assert terminal_skill.matches_command("powershell: Get-Location")


def test_does_not_match_open_powershell(terminal_skill):
    """'abrí powershell' debe ir al AppLauncher, no a TerminalSkill."""
    assert not terminal_skill.matches_command("abrí powershell")
    assert not terminal_skill.matches_command("abre powershell")
    assert not terminal_skill.matches_command("ejecuta powershell")


def test_does_not_match_question_about_cmdlets(terminal_skill):
    """'qué es un cmdlet' no debe disparar la skill."""
    assert not terminal_skill.matches_command("qué es un cmdlet?")
    assert not terminal_skill.matches_command("explicame Get-Process")


# ── TerminalSkill execution ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_terminal_skill_runs_read_command(terminal_skill, controller, ctx):
    response = await terminal_skill.handle("/run Get-Location", ctx)
    assert response is not None
    assert "READ" in response
    assert "exit 0" in response


@pytest.mark.asyncio
async def test_terminal_skill_handles_no_controller(terminal_skill, ctx):
    set_terminal_controller(None)  # type: ignore[arg-type]
    response = await terminal_skill.handle("/run Get-Location", ctx)
    assert "no inicializado" in response.lower() or "controller" in response.lower()


# ── LogReader matching ──────────────────────────────────────────────────

def test_log_reader_matches_slash(log_skill):
    assert log_skill.matches_command("/logs")
    assert log_skill.matches_command("/logs errors")
    assert log_skill.matches_command("/logs request abc-123")


def test_log_reader_matches_natural(log_skill):
    assert log_skill.matches_command("verifica los logs")
    assert log_skill.matches_command("ver logs")
    assert log_skill.matches_command("ultimos logs")


# ── LogReader security ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_reader_rejects_disallowed_file(log_skill, ctx):
    response = await log_skill.handle("/logs /etc/passwd 10", ctx)
    assert "no permitido" in response.lower() or "no existe" in response.lower()


@pytest.mark.asyncio
async def test_log_reader_rejects_path_traversal(log_skill, ctx):
    response = await log_skill.handle("/logs ../../../etc/passwd 10", ctx)
    assert "no permitido" in response.lower() or "no existe" in response.lower()


@pytest.mark.asyncio
async def test_log_reader_request_id_sanitization(log_skill, ctx):
    """request_id con chars peligrosos se sanitiza."""
    response = await log_skill.handle("/logs request ../../bad", ctx)
    # No debe crashear; sanitiza a "bad"
    assert response is not None


# ── End-to-end: ejecutar comando + leer log ─────────────────────────────

@pytest.mark.asyncio
async def test_e2e_run_and_verify_log(terminal_skill, log_skill, controller, ctx, monkeypatch, tmp_path):
    log_file = tmp_path / "terminal.log"
    monkeypatch.setattr(controller, "_log_path", log_file)

    # Override del path resolver para que el LogReader vea nuestro tmp dir
    def _fake_resolve(self, fname):
        if fname == "terminal.log":
            return log_file if log_file.exists() else None
        return None
    monkeypatch.setattr(LogReaderSkill, "_resolve", _fake_resolve)

    await terminal_skill.handle("/run Get-Location", ctx)
    response = await log_skill.handle("/logs request rid-skill-test", ctx)
    assert response is not None
    assert "rid-skill-test" in response
