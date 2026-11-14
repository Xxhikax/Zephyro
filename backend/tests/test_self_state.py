"""
Tests for SelfState — dynamic skills integration.

Covers:
- update_from_skills() captures all enabled skills, not just _SKILL_CAPABILITY_MAP entries
- get_core_identity() includes active skill count when skills are loaded
- get_relevant_capabilities() returns skill listing for skill queries
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from core.self_state import SelfState


def _make_skill(name: str, enabled: bool = True) -> MagicMock:
    skill = MagicMock()
    # metadata es un dataclass SkillMetadata (atributo .name), no un dict.
    skill.metadata = SimpleNamespace(name=name)
    skill.enabled = enabled
    return skill


def _make_registry(*skills) -> MagicMock:
    registry = MagicMock()
    registry.get_all_skills.return_value = list(skills)
    return registry


class TestUpdateFromSkills:
    def test_no_registry_clears_state(self):
        ss = SelfState()
        ss._active_skills = ["leftover"]
        ss.update_from_skills(None)
        assert ss._active_skills == []
        assert ss._active_skill_count == 0

    def test_captures_all_enabled_skills(self):
        ss = SelfState()
        registry = _make_registry(
            _make_skill("schedule"),
            _make_skill("vision_query"),
            _make_skill("system-monitor"),
            _make_skill("file_ops"),
        )
        ss.update_from_skills(registry)
        assert set(ss._active_skills) == {"schedule", "vision_query", "system-monitor", "file_ops"}
        assert ss._active_skill_count == 4

    def test_excludes_disabled_skills(self):
        ss = SelfState()
        registry = _make_registry(
            _make_skill("schedule", enabled=True),
            _make_skill("vision_query", enabled=False),
        )
        ss.update_from_skills(registry)
        assert ss._active_skills == ["schedule"]
        assert ss._active_skill_count == 1

    def test_excludes_unnamed_skills(self):
        ss = SelfState()
        unnamed = _make_skill("")
        registry = _make_registry(unnamed, _make_skill("reminders"))
        ss.update_from_skills(registry)
        assert ss._active_skills == ["reminders"]

    def test_known_skill_still_sets_flag(self):
        ss = SelfState()
        registry = _make_registry(_make_skill("system-monitor"))
        ss.update_from_skills(registry)
        assert ss._system_monitor_active is True

    def test_unknown_skill_included_in_list(self):
        ss = SelfState()
        registry = _make_registry(_make_skill("my-custom-skill"))
        ss.update_from_skills(registry)
        assert "my-custom-skill" in ss._active_skills

    def test_empty_registry_clears_list(self):
        ss = SelfState()
        ss._active_skills = ["old"]
        ss._active_skill_count = 1
        ss.update_from_skills(_make_registry())
        assert ss._active_skills == []
        assert ss._active_skill_count == 0


class TestGetCoreIdentity:
    def test_no_skills_no_count(self):
        ss = SelfState()
        identity = ss.get_core_identity()
        assert "skills activas" not in identity

    def test_with_skills_shows_count(self):
        ss = SelfState()
        registry = _make_registry(
            _make_skill("schedule"),
            _make_skill("file_ops"),
            _make_skill("reminders"),
        )
        ss.update_from_skills(registry)
        identity = ss.get_core_identity()
        assert "3 skills activas" in identity

    def test_contains_name_and_owner(self):
        ss = SelfState()
        identity = ss.get_core_identity()
        assert "Zephyro" in identity
        assert "Bruno" in identity

    def test_never_deny_reminder(self):
        ss = SelfState()
        identity = ss.get_core_identity()
        assert "NUNCA negar" in identity


class TestGetRelevantCapabilitiesSkills:
    def test_skills_activas_query(self):
        ss = SelfState()
        registry = _make_registry(
            _make_skill("schedule"),
            _make_skill("vision_query"),
        )
        ss.update_from_skills(registry)
        result = ss.get_relevant_capabilities("qué skills activas tienes?")
        assert result is not None
        assert "schedule" in result
        assert "vision_query" in result
        assert "2" in result

    def test_skills_disponibles_query(self):
        ss = SelfState()
        registry = _make_registry(_make_skill("reminders"))
        ss.update_from_skills(registry)
        result = ss.get_relevant_capabilities("cuáles son tus skills disponibles")
        assert result is not None
        assert "reminders" in result

    def test_no_skills_loaded_message(self):
        ss = SelfState()
        ss.update_from_skills(_make_registry())
        result = ss.get_relevant_capabilities("qué skills tienes")
        assert result is not None
        assert "No hay skills" in result

    def test_skill_query_before_capability_generic(self):
        ss = SelfState()
        registry = _make_registry(_make_skill("schedule"))
        ss.update_from_skills(registry)
        result = ss.get_relevant_capabilities("qué skills tienes activas")
        # Should return skill listing, not the generic capabilities list
        assert result is not None
        assert "schedule" in result
        assert "Self State — Skills activas" in result

    def test_unrelated_query_returns_none(self):
        ss = SelfState()
        result = ss.get_relevant_capabilities("cómo está el tiempo hoy")
        assert result is None


class TestAutomationDenialDetector:
    """El detector automation_denial debe exigir una frase de negación REAL,
    igual que vision/memory/voice — no disparar por mera co-ocurrencia verbo+app."""

    def test_correct_response_not_flagged(self):
        """Respuesta correcta que menciona la app NO debe registrarse como bug."""
        ss = SelfState()
        result = ss._check_capability_mismatch(
            "ejecutar terminal para diagnóstico",
            "puedo ejecutar la terminal para vos sin problema",
        )
        assert result is None

    def test_helpful_mention_in_response_not_flagged(self):
        """Mencionar 'firefox' en una respuesta útil NO debe disparar denial."""
        ss = SelfState()
        result = ss._check_capability_mismatch(
            "abre firefox",
            "listo, abriendo firefox en un momento",
        )
        assert result is None

    def test_real_denial_still_flagged(self):
        """Una negación REAL sí debe seguir detectándose."""
        ss = SelfState()
        result = ss._check_capability_mismatch(
            "abre firefox",
            "no puedo abrir aplicaciones como firefox",
        )
        assert result is not None
        assert result["type"] == "automation_denial"
        assert result["app"] == "firefox"

    def test_real_denial_no_acceso_phrase(self):
        """'no tengo acceso' + app también debe detectarse."""
        ss = SelfState()
        result = ss._check_capability_mismatch(
            "abre discord",
            "no tengo acceso para abrir discord directamente",
        )
        assert result is not None
        assert result["type"] == "automation_denial"


class TestCanvasDenialDetector:
    """canvas_denial: el LLM niega la integración con Canvas que sí existe
    (bug en vivo 2026-07-02: 'no tengo forma de filtrar por curso...')."""

    def test_denial_flagged(self):
        ss = SelfState()
        result = ss._check_capability_mismatch(
            "revisa si tengo tareas pendientes en canvas",
            "lamentablemente, no tengo forma de filtrar específicamente por curso",
        )
        assert result is not None
        assert result["type"] == "canvas_denial"

    def test_explicit_denial_flagged(self):
        ss = SelfState()
        result = ss._check_capability_mismatch(
            "qué tengo en canvas",
            "no puedo acceder a canvas desde acá",
        )
        assert result is not None
        assert result["type"] == "canvas_denial"

    def test_correct_canvas_answer_not_flagged(self):
        ss = SelfState()
        result = ss._check_capability_mismatch(
            "revisa si tengo pendientes en canvas",
            "tenés 2 entregas próximas en canvas: tarea 3 de redes, informe de cálculo",
        )
        assert result is None
