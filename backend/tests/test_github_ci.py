# -*- coding: utf-8 -*-
"""Tests de la extensión CI/CD de la skill `github` (Fase 0 del roadmap).

No se invoca `gh` acá: se testea el matching, el parseo de acciones y el
formateo del detalle de una corrida con payloads fabricados. Lo importante es
(a) que las frases de CI ruteen a esta skill, (b) que no se le roben inputs a
git_ops / expenses, y (c) que CI siga siendo read-only.
"""
from pathlib import Path

import pytest

from skills.builtin.github.skill import _relative_age
from skills.loader import SkillLoader
from skills.registry import SkillRegistry

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def gh_skill():
    r = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(r)
    sk = r.get_skill("github")
    assert sk is not None, "la skill github no se cargó"
    return sk


# ── Matching ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "/ci",
    "/ci fail",
    "/runs",
    "/gh run list",
    "/gh run view 123",
    "cómo va el build",
    "cómo va el ci",
    "estado del pipeline",
    "por qué falló el build",
    "pasaron los tests en github",
])
def test_matches_ci_inputs(gh_skill, text):
    assert gh_skill.matches_command(text) is True


@pytest.mark.parametrize("text", [
    "git status",            # git_ops
    "/gastos",               # expenses
    "gasté 25 en el almuerzo",
    "qué tengo en canvas",   # canvas
    "cómo va mi día",        # chat
])
def test_does_not_steal_other_domains(gh_skill, text):
    assert gh_skill.matches_command(text) is False


# ── Parseo de acciones ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text,action,args", [
    ("/ci", "list_runs", ""),
    ("/runs", "list_runs", ""),
    ("/gh run list", "list_runs", ""),
    ("cómo va el build", "list_runs", ""),
    ("/ci fail", "why_failed", ""),
    ("por qué falló el build", "why_failed", ""),
    ("/gh run view 4321", "view_run", "4321"),
])
def test_parse_action(gh_skill, text, action, args):
    assert gh_skill._parse_action(text) == (action, args)


def test_existing_actions_still_parse(gh_skill):
    """La extensión de CI no debe pisar las acciones que ya existían."""
    assert gh_skill._parse_action("/gh pr list")[0] == "list_pr"
    assert gh_skill._parse_action("/gh issue list")[0] == "list_issue"
    assert gh_skill._parse_action("/gh pr view 7") == ("view_pr", "7")
    assert gh_skill._parse_action("/gh search fastapi") == ("search", "fastapi")
    assert gh_skill._parse_action("clona el repo Xxhikax/Zephyro")[0] == "clone"


def test_ci_is_read_only(gh_skill):
    """No hay acción que dispare CI: rerun/workflow run no están expuestos."""
    actions = {
        gh_skill._parse_action(t)[0]
        for t in ("/ci", "/ci fail", "/ci rerun", "/gh run view 1", "cómo va el ci")
    }
    assert actions <= {"list_runs", "why_failed", "view_run"}


# ── Formateo ─────────────────────────────────────────────────────────────────

def test_format_run_detail_success(gh_skill):
    out = gh_skill._format_run_detail({
        "databaseId": 42,
        "workflowName": "tests",
        "displayTitle": "feat: algo",
        "status": "completed",
        "conclusion": "success",
        "headBranch": "master",
        "createdAt": "",
        "jobs": [{"name": "pytest", "conclusion": "success", "steps": []}],
    })
    assert "Run #42" in out
    assert "success" in out
    assert "ninguno fallido" in out


def test_format_run_detail_lists_failed_jobs_and_steps(gh_skill):
    out = gh_skill._format_run_detail({
        "databaseId": 43,
        "workflowName": "tests",
        "displayTitle": "fix: algo",
        "status": "completed",
        "conclusion": "failure",
        "headBranch": "feat/x",
        "createdAt": "",
        "jobs": [
            {"name": "lint", "conclusion": "success", "steps": []},
            {"name": "pytest", "conclusion": "failure", "steps": [
                {"name": "Run pytest", "conclusion": "failure"},
                {"name": "Checkout", "conclusion": "success"},
            ]},
        ],
    })
    assert "Jobs fallidos" in out
    assert "pytest" in out
    assert "Run pytest" in out
    assert "lint" not in out.split("Jobs fallidos")[1]


def test_relative_age():
    assert _relative_age("") == "?"
    assert _relative_age("no-es-fecha") == "?"
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    assert _relative_age(recent) == "hace 3h"
