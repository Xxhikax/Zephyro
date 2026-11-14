"""Tests E2E para GitHubSkill, IntrospectionSkill y regresión App vs Terminal."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from unittest.mock import patch

import pytest

from core.platform import IS_WINDOWS
from core.request_context import RequestContext
from modules.automation.terminal_controller import (
    TerminalController, set_terminal_controller, TerminalResult,
)
from modules.code_analysis import binary_finder
from skills.base import SkillMetadata
from skills.builtin.github.skill import GitHubSkill
from skills.builtin.introspection.skill import IntrospectionSkill


pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only")


@pytest.fixture
def ctx():
    return RequestContext(
        user_role="admin", user_name="test", authenticated=True,
        session_id="s1", request_id="rid-e2e", channel_type="websocket",
    )


@pytest.fixture
def controller():
    c = TerminalController(approval_gate=None)
    set_terminal_controller(c)
    yield c
    set_terminal_controller(None)  # type: ignore[arg-type]


@pytest.fixture
def gh_skill(tmp_path):
    md = SkillMetadata(
        name="github", description="g",
        triggers=["/gh", "/repo", "/pr", "/issue"],
    )
    return GitHubSkill(metadata=md, skill_dir=tmp_path)


@pytest.fixture
def introspection_skill(tmp_path):
    md = SkillMetadata(
        name="introspection", description="i",
        triggers=["/analiza", "/introspect"],
    )
    return IntrospectionSkill(metadata=md, skill_dir=tmp_path)


# ── GitHubSkill: parsing ────────────────────────────────────────────────

def test_github_parse_clone_natural(gh_skill):
    action, args = gh_skill._parse_action("instala el repo de tiangolo/fastapi")
    assert action == "clone"
    assert args == "tiangolo/fastapi"


def test_github_parse_view_repo(gh_skill):
    action, args = gh_skill._parse_action("/gh repo view tiangolo/fastapi")
    assert action == "view_repo"
    assert args == "tiangolo/fastapi"


def test_github_parse_pr_view(gh_skill):
    action, args = gh_skill._parse_action("/gh pr view 42")
    assert action == "view_pr"
    assert args == "42"


def test_github_parse_search(gh_skill):
    action, args = gh_skill._parse_action("/gh search fastapi")
    assert action == "search"
    assert "fastapi" in args


def test_github_parse_url(gh_skill):
    action, args = gh_skill._parse_action(
        "instala https://github.com/sst/opencode"
    )
    assert action == "clone"
    assert args == "sst/opencode"


# ── GitHubSkill: gh no instalado ────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_skill_disabled_when_gh_missing(gh_skill, ctx):
    with patch.object(binary_finder, "find", return_value=None):
        await gh_skill.on_load()
    response = await gh_skill.handle("/gh repo view foo/bar", ctx)
    assert response is not None
    assert "no disponible" in response.lower() or "github cli" in response.lower()


# ── IntrospectionSkill: parsing ─────────────────────────────────────────

def test_introspection_parse_basic(introspection_skill):
    q, repo, want_claude = introspection_skill._parse_input(
        "/analiza cómo funciona el orchestrator"
    )
    assert "orchestrator" in q
    assert want_claude is False


def test_introspection_parse_with_claude_flag(introspection_skill):
    q, repo, want_claude = introspection_skill._parse_input(
        "/analiza con claude la arquitectura"
    )
    assert want_claude is True
    assert "arquitectura" in q


def test_introspection_parse_repo_target(introspection_skill, tmp_path, monkeypatch):
    """Si hay 'fastapi' como primera palabra y existe el repo, lo detecta."""
    repos = tmp_path / "repos"
    repos.mkdir()
    (repos / "myrepo").mkdir()
    monkeypatch.setenv("ZEPHYRO_REPOS_ROOT", str(repos))

    q, repo, _ = introspection_skill._parse_input("/analiza myrepo cómo está")
    assert repo == repos / "myrepo"
    assert "está" in q or "como" in q.lower()


# ── Regresión: app vs terminal ──────────────────────────────────────────

def test_terminal_skill_does_not_intercept_app_open(tmp_path):
    """'abrí powershell' debe NO matchear TerminalSkill (va a AppLauncher)."""
    from skills.builtin.terminal.skill import TerminalSkill
    md = SkillMetadata(
        name="terminal", description="t",
        triggers=["/run", "/exec", "/pwsh",
                  "ejecuta el comando", "ejecutar el comando",
                  "corre el comando", "corre este comando",
                  "powershell:", "pwsh:", "ps:"],
    )
    skill = TerminalSkill(metadata=md, skill_dir=tmp_path)

    # Frases que NO deben disparar TerminalSkill
    no_match = [
        "abrí powershell",
        "abre powershell",
        "abrir powershell",
        "lanza powershell",
        "ejecuta powershell",  # sin comando real → no es TerminalSkill
        "corre el navegador",
        "qué es powershell?",
    ]
    for phrase in no_match:
        assert not skill.matches_command(phrase), f"falso positivo: {phrase!r}"

    # Frases que SÍ deben disparar TerminalSkill
    must_match = [
        "/run Get-Process",
        "/exec git status",
        "/pwsh ls",
        "ejecuta el comando Get-Location",
        "corre el comando python --version",
        "powershell: Get-Process",
        "ps: ls",
    ]
    for phrase in must_match:
        assert skill.matches_command(phrase), f"falso negativo: {phrase!r}"


# ── E2E: TerminalSkill + LogReaderSkill ─────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_terminal_then_log_reader(controller, ctx, tmp_path, monkeypatch):
    """Ejecuta un comando, luego verifica que el log tiene la entrada."""
    log_file = tmp_path / "terminal.log"
    monkeypatch.setattr(controller, "_log_path", log_file)

    from skills.builtin.terminal.skill import TerminalSkill
    from skills.builtin.log_reader.skill import LogReaderSkill

    term_md = SkillMetadata(
        name="terminal", description="t",
        triggers=["/run", "/exec", "/pwsh"],
    )
    log_md = SkillMetadata(name="log-reader", description="l", triggers=["/logs"])

    term = TerminalSkill(metadata=term_md, skill_dir=tmp_path)
    log_skill = LogReaderSkill(metadata=log_md, skill_dir=tmp_path)

    def _fake_resolve(self, fname):
        return log_file if fname == "terminal.log" and log_file.exists() else None
    monkeypatch.setattr(LogReaderSkill, "_resolve", _fake_resolve)

    response_run = await term.handle("/run Get-Location", ctx)
    assert response_run is not None
    assert "exit 0" in response_run

    response_log = await log_skill.handle("/logs request rid-e2e", ctx)
    assert response_log is not None
    assert "rid-e2e" in response_log
    assert "Get-Location" in response_log
