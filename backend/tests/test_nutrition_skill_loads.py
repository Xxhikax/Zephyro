# -*- coding: utf-8 -*-
"""
Regresión: el skill de nutrición debe registrarse en el SkillRegistry.

Bug en vivo: nutrition/SKILL.md declaraba `requires.python: [psycopg2-binary]`
(nombre del paquete pip, no del módulo importable) → check_requirements() hacía
__import__("psycopg2-binary") → ModuleNotFoundError → el loader descartaba el
skill en CADA arranque ("Skill nutrition: falta python:psycopg2-binary" en
web_app.stderr.log) y todos sus triggers de voz quedaban inalcanzables.
Mismo bug de clase que el Fix A de canvas (requires.env).
"""
import os
from pathlib import Path

import pytest

from skills.registry import SkillRegistry
from skills.loader import SkillLoader

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"

# In production PGHOST/PGDATABASE come from backend/.env (load_dotenv in
# web_app.py). Tests run without dotenv, so provide defaults — the unit under
# test is the importable-module-name fix, not the Postgres configuration.
os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGDATABASE", "zephyro")


@pytest.fixture(scope="module")
def reg():
    r = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(r)
    return r


def test_nutrition_registers(reg):
    # requires.python must list the importable module (psycopg2), not the pip
    # package name (psycopg2-binary), or the loader silently drops the skill.
    assert reg.get_skill("nutrition") is not None


def test_nutrition_triggers_reachable(reg):
    nutrition = reg.get_skill("nutrition")
    if nutrition is None:
        pytest.skip("nutrition no cargó")
    assert nutrition.matches_command("/macros") is True
