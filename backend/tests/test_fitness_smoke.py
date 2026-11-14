"""Smoke tests para modulo de fitness (imports, signatures, sin tocar DB)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestFitnessImports:
    def test_db_module_imports(self):
        from modules.fitness import db
        assert db is not None

    def test_service_module_imports(self):
        from modules.fitness import service
        assert hasattr(service, "FitnessService")
        assert hasattr(service, "get_fitness_service")

    def test_schema_module_imports(self):
        from modules.fitness import schema
        assert schema is not None

    def test_prompts_module_imports(self):
        from modules.fitness import prompts
        assert prompts is not None


class TestFitnessServiceSurface:
    def test_service_has_expected_methods(self):
        from modules.fitness.service import FitnessService
        expected = [
            "get_profile", "update_profile", "set_preferred_days",
            "generate_home_plan", "get_active_plan", "archive_plan",
            "upgrade_plan", "materialize_plan_to_agenda",
            "auto_reschedule_session", "check_and_resolve_conflicts",
            "suggest_alternative_slots",
            "log_session", "verify_session",
            "get_streak", "get_weekly_summary",
            "set_llm_manager", "set_image_analyzer", "set_channel_manager",
        ]
        missing = [m for m in expected if not hasattr(FitnessService, m)]
        assert not missing, f"Métodos faltantes: {missing}"

    def test_get_service_returns_singleton(self):
        from modules.fitness.service import get_fitness_service
        a = get_fitness_service()
        b = get_fitness_service()
        assert a is b


class TestFitnessSkillImports:
    def test_skill_module_imports(self):
        import sys
        from pathlib import Path
        # Skills viven en backend/skills/builtin/fitness/skill.py — el loader
        # las carga dinámicamente, pero podemos importar directo.
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "builtin" / "fitness"
        sys.path.insert(0, str(skill_path))
        try:
            import skill as fitness_skill_module
            assert hasattr(fitness_skill_module, "FitnessSkill")
        finally:
            sys.path.pop(0)
