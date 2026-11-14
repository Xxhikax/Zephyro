# -*- coding: utf-8 -*-
"""
Barrido de voseo en skills: las skills builtin reconocen el habla rioplatense
("agendá", "poné", "buscá", "recordame", "leé") aunque sus triggers estén en
tuteo. La normalización vive en skills.base.normalize_voseo + NFC, así una sola
pieza cubre las 16 skills (las que usan matching default y los overrides).
"""
from pathlib import Path

import pytest

from skills.base import normalize_voseo
from skills.registry import SkillRegistry
from skills.loader import SkillLoader

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def registry():
    reg = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(reg)
    return reg


@pytest.mark.parametrize("voseo,tuteo", [
    ("agendá cálculo el lunes", "agenda cálculo el lunes"),
    ("poné en mi agenda gym", "pon en mi agenda gym"),
    ("buscá repo zephyro", "busca repo zephyro"),
    ("revisá los logs", "revisa los logs"),
    ("leé el archivo test.py", "lee el archivo test.py"),
    ("recordame comprar pan", "recuérdame comprar pan"),
    ("reprogramá mi entreno", "reprograma mi entreno"),
    ("mové el evento 3", "mueve el evento 3"),
])
def test_normalize_voseo(voseo, tuteo):
    assert normalize_voseo(voseo) == tuteo


@pytest.mark.parametrize("skill_name,text", [
    ("schedule", "agendá cálculo el lunes a las 7"),
    ("schedule", "poné en mi agenda gym mañana"),
    ("reminders", "recordame comprar pan"),
    ("file-ops", "leé el archivo test.py"),
    ("github", "buscá repo zephyro"),
    ("terminal", "ejecutá el comando ls"),
    ("log-reader", "revisá los logs"),
])
def test_registry_routes_voseo(registry, skill_name, text):
    # El registry normaliza voseo en el punto de despacho, así que cubre TODAS
    # las skills (overrides propios y default). rank_candidates refleja ese flujo.
    if registry.get_skill(skill_name) is None:
        pytest.skip(f"skill {skill_name} no cargada (requirements)")
    names = [c.name for c in registry.rank_candidates(text)]
    assert skill_name in names, \
        f"{skill_name} no apareció como candidato para voseo {text!r}; got {names}"


def test_casual_chat_no_candidates(registry):
    # Anti-fricción: charla casual no debe rankear skills de comando.
    names = [c.name for c in registry.rank_candidates("qué aburrido está el día")]
    assert "schedule" not in names and "reminders" not in names
