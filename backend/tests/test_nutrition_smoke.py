"""Smoke + lógica IR para modulo de nutrición (sin tocar DB)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestNutritionImports:
    def test_modules_import(self):
        from modules.nutrition import db, service, prompts, peru_food, reminders
        assert db and service and prompts and peru_food and reminders

    def test_service_class_exists(self):
        from modules.nutrition.service import NutritionService, get_nutrition_service
        assert NutritionService is not None
        assert callable(get_nutrition_service)


class TestNutritionServiceSurface:
    def test_expected_methods(self):
        from modules.nutrition.service import NutritionService
        expected = [
            "get_profile", "update_profile",
            "analyze_food_photo", "analyze_food_text",
            "log_food",
            "get_daily_summary", "suggest_next_meal",
            "log_water", "log_steps", "log_weight",
            "get_weekly_nutrition_report", "get_weight_trend",
        ]
        missing = [m for m in expected if not hasattr(NutritionService, m)]
        assert not missing, f"Métodos faltantes: {missing}"


class TestPeruFoodCatalog:
    def test_catalog_non_empty(self):
        from modules.nutrition.peru_food import PERU_FOOD
        assert isinstance(PERU_FOOD, dict)
        assert len(PERU_FOOD) >= 10

    def test_lookup_dish_finds_ceviche(self):
        from modules.nutrition.peru_food import lookup_dish
        result = lookup_dish("ceviche")
        assert result is not None

    def test_ceviche_is_not_flagged(self):
        from modules.nutrition.peru_food import lookup_dish
        result = lookup_dish("ceviche")
        assert result is not None
        assert result.get("flag") is False

    def test_mazamorra_is_flagged(self):
        from modules.nutrition.peru_food import lookup_dish
        result = lookup_dish("mazamorra morada")
        assert result is not None
        assert result.get("flag") is True

    def test_chicha_morada_flagged_for_sugar(self):
        from modules.nutrition.peru_food import lookup_dish
        result = lookup_dish("chicha morada")
        assert result is not None
        assert result.get("flag") is True

    def test_unknown_dish_returns_none_or_safe(self):
        from modules.nutrition.peru_food import lookup_dish
        result = lookup_dish("xxxxxxxx-not-a-dish-xxxxxxxx")
        # Aceptamos None o un default explícito
        assert result is None or isinstance(result, dict)


class TestInsulinRules:
    """Verifica que el sistema flaggea high-GI keywords sin LLM."""

    def test_module_has_high_gi_keywords(self):
        from modules.nutrition import service
        # buscar el set en el módulo
        assert any(
            "arroz blanco" in str(getattr(service, k, "")).lower()
            or (isinstance(getattr(service, k, None), (set, frozenset, list, tuple))
                and any("arroz" in str(x).lower() for x in getattr(service, k, [])))
            for k in dir(service)
        ), "No encuentro _HIGH_GI_KEYWORDS o similar en nutrition.service"

    def test_module_has_nudge_templates(self):
        from modules.nutrition import service
        found = any(
            isinstance(getattr(service, k, None), dict)
            and any("insulina" in str(v).lower() for v in getattr(service, k, {}).values())
            for k in dir(service)
        )
        assert found, "No encuentro _NUDGE_TEMPLATES con mensajes sobre insulina"
