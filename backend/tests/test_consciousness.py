"""
Tests para el sistema de pseudo-consciencia proactiva (Phase 3).

Cubre:
  TestPreconditionGuards   (6) — guards individuales
  TestConsciousnessSnapshot (4) — generación de snapshots
  TestConsciousness         (5) — pre_execute / introspect
  TestOrchestratorIntegration (2) — smoke tests sin LLM real
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Añadir backend al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.precondition_guards import (
    GuardResult,
    PRECONDITIONS,
    check_playwright_installed,
    check_skill_health,
    check_source_recency,
    run_guards,
    verify_browser_binaries,
    verify_current_date,
)
from core.consciousness_snapshot import ConsciousnessSnapshot
from core.consciousness import Consciousness, ExecutionDecision, get_consciousness, reset_consciousness
from core.event_bus import reset_bus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_ls(skills: dict | None = None, issues: list | None = None) -> MagicMock:
    """Crea un mock de LivingState con estado configurable."""
    ls = MagicMock()
    state = {
        "skills": skills or {},
        "active_issues": issues or [],
        "recent_changes": [],
        "system": {"restart_count": 0, "uptime_since": "2026-01-01T00:00:00"},
    }
    ls._state = state
    ls.get_skill_health.side_effect = lambda name: dict(state["skills"].get(name, {
        "status": "unknown", "consecutive_failures": 0,
    }))
    ls.get_active_issues.return_value = [
        i for i in state["active_issues"] if i.get("status") != "resolved"
    ]
    ls.get_recent_changes.side_effect = lambda n=5: state["recent_changes"][:n]
    return ls


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TestPreconditionGuards
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreconditionGuards(unittest.TestCase):

    def test_verify_current_date_always_passes(self):
        result = verify_current_date({})
        self.assertTrue(result.passed)
        self.assertIn("verified_date", result.enrichment)
        # Formato YYYY-MM-DD
        d = result.enrichment["verified_date"]
        datetime.strptime(d, "%Y-%m-%d")  # no lanza

    def test_verify_current_date_enriches_time(self):
        result = verify_current_date({})
        self.assertIn("verified_time", result.enrichment)
        t = result.enrichment["verified_time"]
        # Formato HH:MM
        datetime.strptime(t, "%H:%M")

    def test_check_source_recency_detects_temporal(self):
        for keyword in ("hoy", "ahora", "today", "reciente", "latest"):
            with self.subTest(keyword=keyword):
                result = check_source_recency({"user_input": f"noticias {keyword}"})
                self.assertTrue(result.passed)
                self.assertTrue(result.enrichment.get("has_temporal_intent"))
                self.assertIn("verified_date", result.enrichment)

    def test_check_source_recency_ignores_non_temporal(self):
        result = check_source_recency({"user_input": "cómo funciona python"})
        self.assertTrue(result.passed)
        self.assertNotIn("has_temporal_intent", result.enrichment)

    def test_check_skill_health_blocks_broken(self):
        ls = _make_mock_ls(skills={
            "my_skill": {"status": "broken", "consecutive_failures": 4}
        })
        result = check_skill_health({"skill_name": "my_skill", "living_state": ls})
        self.assertFalse(result.passed)
        self.assertIn("rota", result.reason)
        self.assertIsNotNone(result.suggested_fix)

    def test_check_playwright_installed_fails_when_missing(self):
        with patch("core.precondition_guards.shutil.which", return_value=None):
            result = check_playwright_installed({})
        self.assertFalse(result.passed)
        self.assertIn("playwright", result.reason.lower())
        self.assertIsNotNone(result.suggested_fix)

    def test_verify_browser_binaries_fails_when_missing(self):
        with patch("core.precondition_guards.shutil.which", return_value=None):
            result = verify_browser_binaries({})
        self.assertFalse(result.passed)
        self.assertIsNotNone(result.suggested_fix)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TestConsciousnessSnapshot
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsciousnessSnapshot(unittest.TestCase):

    def _make_snapshot(self, ls=None) -> ConsciousnessSnapshot:
        return ConsciousnessSnapshot(lambda: ls)

    def test_tier1_always_has_date_line(self):
        snap = self._make_snapshot()
        output = snap.generate(tier=1)
        self.assertIn("date:", output)
        self.assertIn("server-verified", output)

    def test_tier1_under_200_tokens_when_clean(self):
        snap = self._make_snapshot()
        output = snap.generate(tier=1)
        # Estimación simple: 1 token ≈ 4 chars; < 200 tokens ≈ < 800 chars
        self.assertLess(len(output), 800, f"Snapshot demasiado largo ({len(output)} chars):\n{output}")

    def test_tier1_shows_degraded_skill(self):
        ls = _make_mock_ls(skills={
            "browser_ops": {"status": "degraded", "consecutive_failures": 2}
        })
        snap = self._make_snapshot(ls)
        output = snap.generate(tier=1)
        self.assertIn("skills_degraded", output)
        self.assertIn("browser_ops", output)

    def test_daily_counter_resets_on_new_day(self):
        snap = self._make_snapshot()
        # Simular fallo de ayer
        snap._failed_date = date.today() - timedelta(days=1)
        snap._failed_today = {"some_guard": 3}
        # Llamar record_guard_failure debe resetear el contador antes de agregar
        snap.record_guard_failure("new_guard")
        self.assertNotIn("some_guard", snap._failed_today)
        self.assertEqual(snap._failed_today.get("new_guard"), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TestConsciousness
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsciousness(unittest.TestCase):

    def setUp(self):
        reset_bus()
        reset_consciousness()

    def tearDown(self):
        reset_bus()
        reset_consciousness()

    def _make_consciousness(self, ls=None) -> Consciousness:
        c = Consciousness()
        c.initialize(lambda: ls)
        return c

    def test_pre_execute_temporal_search_passes_with_date(self):
        c = self._make_consciousness()
        decision = c.pre_execute("temporal_search", "accidentes hoy en Lima")
        self.assertTrue(decision.allowed)
        self.assertIn("date:", decision.enriched_context)
        self.assertIn("server-verified", decision.enriched_context)

    def test_pre_execute_browser_action_blocks_when_no_browser(self):
        with patch("core.precondition_guards.shutil.which", return_value=None):
            c = self._make_consciousness()
            decision = c.pre_execute("browser_action", "abre youtube")
        self.assertFalse(decision.allowed)
        self.assertIsNotNone(decision.suggested_fix)

    def test_pre_execute_llm_call_passes_with_api_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            c = self._make_consciousness()
            decision = c.pre_execute("llm_call", "qué es python")
        self.assertTrue(decision.allowed)

    def test_introspect_with_active_issues(self):
        ls = _make_mock_ls(issues=[{
            "id": "abc123",
            "module": "orchestrator",
            "error": "Connection refused",
            "detected_at": "2026-04-11T10:00:00",
            "status": "open",
        }])
        c = self._make_consciousness(ls)
        report = c.introspect()
        self.assertIn("orchestrator", report)
        self.assertIn("Connection refused", report)

    def test_introspect_clean_state(self):
        ls = _make_mock_ls()
        c = self._make_consciousness(ls)
        report = c.introspect()
        self.assertIn("Sin issues", report)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TestOrchestratorIntegration (smoke tests — sin LLM real)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrchestratorIntegration(unittest.TestCase):
    """
    Smoke tests que verifican que el ExecutionDecision lleva los datos correctos.
    Sin LLM real — solo prueban la lógica de pre_execute().
    """

    def setUp(self):
        reset_bus()
        reset_consciousness()

    def tearDown(self):
        reset_bus()
        reset_consciousness()

    def test_temporal_query_enriched_context_has_date(self):
        c = Consciousness()
        c.initialize(lambda: None)
        decision = c.pre_execute(
            "temporal_search",
            user_input="noticias de hoy",
            analysis={"needs_web_search": True},
        )
        self.assertTrue(decision.allowed)
        self.assertIn("date:", decision.enriched_context)
        # Fecha en formato YYYY-MM-DD
        self.assertRegex(decision.enriched_context, r"date: \d{4}-\d{2}-\d{2}")

    def test_broken_skill_returns_allowed_false_with_fix(self):
        ls = _make_mock_ls(skills={
            "file_ops": {"status": "broken", "consecutive_failures": 5}
        })
        c = Consciousness()
        c.initialize(lambda: ls)
        decision = c.pre_execute(
            "skill_execution",
            skill_name="file_ops",
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(bool(decision.suggested_fix))


if __name__ == "__main__":
    unittest.main(verbosity=2)
