"""
Invariantes estructurales de las skills builtin.

Atrapa antes de producción: skills sin SKILL.md, sin skill.py, frontmatter roto,
o sin triggers (que se cargan pero el router nunca rutea). El caso vision_query
(triggers: [] + override de matches_command) es una excepción documentada.
"""
from pathlib import Path

import pytest
import yaml

BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"

# Skills que legítimamente tienen triggers vacío porque overridean matches_command.
_NO_TRIGGER_EXCEPTIONS = {"vision_query"}


def _skill_dirs():
    if not BUILTIN.exists():
        return []
    return [d for d in sorted(BUILTIN.iterdir())
            if d.is_dir() and not d.name.startswith("_")]


def _parse_frontmatter(skill_md: Path) -> dict:
    raw = skill_md.read_text(encoding="utf-8")
    assert raw.startswith("---"), f"{skill_md}: no empieza con frontmatter ---"
    parts = raw.split("---", 2)
    assert len(parts) >= 3, f"{skill_md}: frontmatter mal delimitado"
    data = yaml.safe_load(parts[1])
    assert isinstance(data, dict), f"{skill_md}: frontmatter no es un dict"
    return data


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda d: d.name)
def test_skill_has_required_files(skill_dir):
    assert (skill_dir / "SKILL.md").exists(), f"{skill_dir.name}: falta SKILL.md"
    assert (skill_dir / "skill.py").exists(), f"{skill_dir.name}: falta skill.py"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda d: d.name)
def test_skill_frontmatter_valid(skill_dir):
    data = _parse_frontmatter(skill_dir / "SKILL.md")
    assert data.get("name"), f"{skill_dir.name}: falta 'name' en frontmatter"
    assert "description" in data, f"{skill_dir.name}: falta 'description'"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda d: d.name)
def test_skill_has_triggers_or_is_exception(skill_dir):
    data = _parse_frontmatter(skill_dir / "SKILL.md")
    triggers = data.get("triggers") or []
    if skill_dir.name in _NO_TRIGGER_EXCEPTIONS:
        # Debe overridear matches_command para justificar triggers vacío.
        src = (skill_dir / "skill.py").read_text(encoding="utf-8")
        assert "def matches_command" in src, (
            f"{skill_dir.name}: está en la whitelist de triggers vacíos pero "
            f"no overridea matches_command")
        return
    assert triggers, (
        f"{skill_dir.name}: triggers vacío y no está en la whitelist de "
        f"excepciones — la skill se cargaría pero el router no la rutearía")


def test_at_least_some_skills_exist():
    assert len(_skill_dirs()) > 0, "No se encontraron skills builtin"
