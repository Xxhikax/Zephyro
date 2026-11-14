"""
Tests para EventBus y LivingState.

Cubre:
  - EventBus: pub/sub, múltiples listeners, aislamiento de excepciones, unsubscribe, singleton
  - Persistencia SQLite: load/save round-trip, estado vacío, mark_issue_resolved
  - Skill health tracking: EMA, consecutive_failures, status transitions
  - Issue tracking: creación, dedup, resolución, AUTO_DIAGNOSE
  - snapshot_for_llm: tier 1/2/3
  - Git change tracking: _on_git_commit
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Asegurarse de que el directorio backend esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Resetear el singleton del bus entre tests
from core.event_bus import reset_bus


def make_living_state(tmp_dir: str) -> "LivingState":
    """Factory de LivingState con paths aislados en tmp_dir."""
    # Importar aquí para que el reset_bus tenga efecto antes
    from core.living_state import LivingState

    db_path = str(Path(tmp_dir) / "living_state.db")
    log_file = str(Path(tmp_dir) / "backend.log")
    # Crear log file vacío para que el watcher no se queje
    Path(log_file).touch()
    return LivingState(
        db_path=db_path,
        code_root=tmp_dir,
        log_file=log_file,
    )


# ════════════════════════════════════════════════════════════════════════════
# TestEventBus
# ════════════════════════════════════════════════════════════════════════════

class TestEventBus(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        reset_bus()

    def tearDown(self) -> None:
        reset_bus()

    async def test_publish_reaches_listener(self) -> None:
        from core.event_bus import get_bus

        received: list[dict] = []

        async def handler(payload: dict) -> None:
            received.append(payload)

        bus = get_bus()
        bus.subscribe("test_event", handler)
        bus.publish("test_event", {"data": 42})
        # dar tiempo a la tarea de completarse
        await asyncio.sleep(0.05)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["data"], 42)

    async def test_multiple_listeners_all_called(self) -> None:
        from core.event_bus import get_bus

        calls: list[str] = []

        async def h1(p: dict) -> None:
            calls.append("h1")

        async def h2(p: dict) -> None:
            calls.append("h2")

        bus = get_bus()
        bus.subscribe("ev", h1)
        bus.subscribe("ev", h2)
        bus.publish("ev", {})
        await asyncio.sleep(0.05)
        self.assertIn("h1", calls)
        self.assertIn("h2", calls)

    async def test_exception_in_listener_does_not_break_bus(self) -> None:
        from core.event_bus import get_bus

        ok: list[bool] = []

        async def bad_handler(p: dict) -> None:
            raise ValueError("boom")

        async def good_handler(p: dict) -> None:
            ok.append(True)

        bus = get_bus()
        bus.subscribe("ev", bad_handler)
        bus.subscribe("ev", good_handler)
        bus.publish("ev", {})
        await asyncio.sleep(0.05)
        self.assertTrue(ok)

    async def test_unsubscribe_stops_delivery(self) -> None:
        from core.event_bus import get_bus

        calls: list[int] = []

        async def handler(p: dict) -> None:
            calls.append(1)

        bus = get_bus()
        bus.subscribe("ev", handler)
        bus.publish("ev", {})
        await asyncio.sleep(0.05)
        self.assertEqual(len(calls), 1)

        bus.unsubscribe("ev", handler)
        bus.publish("ev", {})
        await asyncio.sleep(0.05)
        self.assertEqual(len(calls), 1)  # sin cambio

    async def test_singleton_is_idempotent(self) -> None:
        from core.event_bus import get_bus

        bus1 = get_bus()
        bus2 = get_bus()
        self.assertIs(bus1, bus2)

    async def test_no_listeners_publish_is_noop(self) -> None:
        from core.event_bus import get_bus

        # No debe lanzar excepción
        get_bus().publish("unknown_event", {"x": 1})
        await asyncio.sleep(0.02)


# ════════════════════════════════════════════════════════════════════════════
# TestLivingStatePersistence
# ════════════════════════════════════════════════════════════════════════════

class TestLivingStatePersistence(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        reset_bus()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        reset_bus()

    async def test_empty_state_on_new_db(self) -> None:
        ls = make_living_state(self.tmp)
        ls._load_state()
        self.assertEqual(ls._state["skills"], {})
        self.assertEqual(ls._state["active_issues"], [])
        self.assertEqual(ls._state["recent_changes"], [])

    async def test_save_and_load_round_trip(self) -> None:
        ls = make_living_state(self.tmp)
        ls._load_state()
        ls._state["current_version"] = "abc123"
        ls._save_state()

        ls2 = make_living_state(self.tmp)
        ls2._load_state()
        self.assertEqual(ls2._state["current_version"], "abc123")

    async def test_issues_persisted_across_instances(self) -> None:
        ls = make_living_state(self.tmp)
        ls._load_state()
        ls._state["active_issues"].append({
            "id": "test-id",
            "module": "test_mod",
            "error": "test error",
            "detected_at": "2026-01-01T00:00:00+00:00",
            "status": "open",
        })
        ls._save_state()

        ls2 = make_living_state(self.tmp)
        ls2._load_state()
        self.assertEqual(len(ls2._state["active_issues"]), 1)
        self.assertEqual(ls2._state["active_issues"][0]["id"], "test-id")

    async def test_mark_issue_resolved(self) -> None:
        ls = make_living_state(self.tmp)
        ls._load_state()
        ls._state["active_issues"].append({
            "id": "resolve-me",
            "module": "mod",
            "error": "err",
            "detected_at": "2026-01-01T00:00:00+00:00",
            "status": "open",
        })

        result = ls.mark_issue_resolved("resolve-me")
        self.assertTrue(result)
        self.assertEqual(
            ls._state["active_issues"][0]["status"], "resolved"
        )
        # get_active_issues no debe incluirlo
        self.assertEqual(ls.get_active_issues(), [])

    async def test_mark_issue_resolved_unknown_id_returns_false(self) -> None:
        ls = make_living_state(self.tmp)
        ls._load_state()
        result = ls.mark_issue_resolved("nonexistent")
        self.assertFalse(result)


# ════════════════════════════════════════════════════════════════════════════
# TestSkillHealthTracking
# ════════════════════════════════════════════════════════════════════════════

class TestSkillHealthTracking(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        reset_bus()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        reset_bus()

    def _make_ls(self) -> "LivingState":
        ls = make_living_state(self.tmp)
        ls._load_state()
        return ls

    async def test_success_creates_skill_entry(self) -> None:
        ls = self._make_ls()
        await ls._on_skill_executed({"skill_name": "my_skill", "success": True})
        self.assertIn("my_skill", ls._state["skills"])
        self.assertEqual(ls._state["skills"]["my_skill"]["status"], "ok")

    async def test_consecutive_failures_increment(self) -> None:
        ls = self._make_ls()
        await ls._on_skill_executed({"skill_name": "sk", "success": False})
        await ls._on_skill_executed({"skill_name": "sk", "success": False})
        self.assertEqual(ls._state["skills"]["sk"]["consecutive_failures"], 2)

    async def test_success_resets_consecutive_failures(self) -> None:
        ls = self._make_ls()
        await ls._on_skill_executed({"skill_name": "sk", "success": False})
        await ls._on_skill_executed({"skill_name": "sk", "success": False})
        await ls._on_skill_executed({"skill_name": "sk", "success": True})
        self.assertEqual(ls._state["skills"]["sk"]["consecutive_failures"], 0)

    async def test_status_degraded_at_two_failures(self) -> None:
        ls = self._make_ls()
        await ls._on_skill_executed({"skill_name": "sk", "success": False})
        await ls._on_skill_executed({"skill_name": "sk", "success": False})
        self.assertEqual(ls._state["skills"]["sk"]["status"], "degraded")

    async def test_status_broken_at_three_failures(self) -> None:
        ls = self._make_ls()
        for _ in range(3):
            await ls._on_skill_executed({"skill_name": "sk", "success": False})
        self.assertEqual(ls._state["skills"]["sk"]["status"], "broken")

    async def test_success_rate_ema(self) -> None:
        from core.living_state import _EMA_ALPHA
        ls = self._make_ls()
        # Primer evento: initializa a 1.0 (success=True)
        await ls._on_skill_executed({"skill_name": "sk", "success": True})
        rate_after_first = ls._state["skills"]["sk"]["success_rate"]
        self.assertAlmostEqual(rate_after_first, 1.0)
        # Segundo evento: failure → EMA
        await ls._on_skill_executed({"skill_name": "sk", "success": False})
        expected = (1 - _EMA_ALPHA) * 1.0 + _EMA_ALPHA * 0.0
        self.assertAlmostEqual(
            ls._state["skills"]["sk"]["success_rate"], expected, places=5
        )


# ════════════════════════════════════════════════════════════════════════════
# TestIssueTracking
# ════════════════════════════════════════════════════════════════════════════

class TestIssueTracking(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        reset_bus()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        reset_bus()

    def _make_ls(self) -> "LivingState":
        ls = make_living_state(self.tmp)
        ls._load_state()
        return ls

    async def test_new_issue_created_on_log_error(self) -> None:
        ls = self._make_ls()
        await ls._on_log_error({
            "level": "ERROR",
            "logger": "zephyro.web",
            "message": "Something broke",
            "timestamp": "12:00:00",
            "raw": "[ERROR] zephyro.web: Something broke",
        })
        issues = ls.get_active_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["module"], "web")
        self.assertEqual(issues[0]["status"], "open")

    async def test_dedup_prevents_duplicate_issues(self) -> None:
        ls = self._make_ls()
        payload = {
            "level": "ERROR",
            "logger": "zephyro.web",
            "message": "Same error again",
            "timestamp": "12:00:00",
            "raw": "",
        }
        await ls._on_log_error(payload)
        await ls._on_log_error(payload)
        self.assertEqual(len(ls.get_active_issues()), 1)

    async def test_resolved_issue_not_in_get_active_issues(self) -> None:
        ls = self._make_ls()
        await ls._on_log_error({
            "level": "ERROR",
            "logger": "zephyro.mod",
            "message": "err",
            "timestamp": "12:00:00",
            "raw": "",
        })
        issue_id = ls.get_active_issues()[0]["id"]
        ls.mark_issue_resolved(issue_id)
        self.assertEqual(ls.get_active_issues(), [])

    async def test_auto_diagnose_published_on_new_issue(self) -> None:
        from core.event_bus import get_bus, AUTO_DIAGNOSE

        received: list[dict] = []

        async def handler(p: dict) -> None:
            received.append(p)

        get_bus().subscribe(AUTO_DIAGNOSE, handler)

        ls = self._make_ls()
        await ls._on_log_error({
            "level": "ERROR",
            "logger": "zephyro.core",
            "message": "critical failure",
            "timestamp": "12:00:00",
            "raw": "",
        })
        await asyncio.sleep(0.05)
        self.assertTrue(len(received) >= 1)


# ════════════════════════════════════════════════════════════════════════════
# TestSnapshotForLLM
# ════════════════════════════════════════════════════════════════════════════

class TestSnapshotForLLM(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        reset_bus()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        reset_bus()

    def _make_ls(self) -> "LivingState":
        ls = make_living_state(self.tmp)
        ls._load_state()
        return ls

    async def test_tier1_only_degraded_or_broken(self) -> None:
        ls = self._make_ls()
        # skill ok → no debe aparecer en tier 1
        ls._state["skills"]["healthy_skill"] = {
            "status": "ok", "last_run": "2026-01-01T00:00:00+00:00",
            "success_rate": 1.0, "consecutive_failures": 0,
        }
        # skill broken → debe aparecer
        ls._state["skills"]["broken_skill"] = {
            "status": "broken", "last_run": "2026-01-01T00:00:00+00:00",
            "success_rate": 0.0, "consecutive_failures": 5,
        }
        snap = ls.snapshot_for_llm(tier=1)
        self.assertIn("broken_skill", snap)
        self.assertNotIn("healthy_skill", snap)

    async def test_tier2_includes_recent_changes(self) -> None:
        ls = self._make_ls()
        ls._state["recent_changes"] = [{
            "commit_hash": "deadbeef",
            "message": "fix something",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "files_changed": ["core/router.py"],
        }]
        snap = ls.snapshot_for_llm(tier=2)
        self.assertIn("deadbeef", snap)
        self.assertIn("fix something", snap)

    async def test_tier3_is_valid_json(self) -> None:
        ls = self._make_ls()
        snap = ls.snapshot_for_llm(tier=3)
        parsed = json.loads(snap)
        self.assertIn("skills", parsed)
        self.assertIn("active_issues", parsed)
        self.assertIn("system", parsed)


# ════════════════════════════════════════════════════════════════════════════
# TestGitChangeTracking
# ════════════════════════════════════════════════════════════════════════════

class TestGitChangeTracking(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        reset_bus()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        reset_bus()

    def _make_ls(self) -> "LivingState":
        ls = make_living_state(self.tmp)
        ls._load_state()
        return ls

    async def test_on_git_commit_updates_recent_changes(self) -> None:
        ls = self._make_ls()
        await ls._on_git_commit({
            "commit_hash": "abc123",
            "message": "feat: add feature",
            "timestamp": "2026-01-01T12:00:00+00:00",
            "files_changed": ["core/router.py"],
        })
        changes = ls.get_recent_changes()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["commit_hash"], "abc123")

    async def test_on_git_commit_updates_current_version(self) -> None:
        ls = self._make_ls()
        await ls._on_git_commit({
            "commit_hash": "deadbeef",
            "message": "chore: update",
            "timestamp": "2026-01-01T12:00:00+00:00",
            "files_changed": [],
        })
        self.assertEqual(ls._state["current_version"], "deadbeef")

    async def test_recent_changes_capped_at_20(self) -> None:
        from core.living_state import _MAX_RECENT_CHANGES
        ls = self._make_ls()
        for i in range(25):
            await ls._on_git_commit({
                "commit_hash": f"hash{i:03d}",
                "message": f"commit {i}",
                "timestamp": "2026-01-01T12:00:00+00:00",
                "files_changed": [],
            })
        self.assertLessEqual(len(ls._state["recent_changes"]), _MAX_RECENT_CHANGES)

    async def test_dedup_same_commit_not_added_twice(self) -> None:
        ls = self._make_ls()
        payload = {
            "commit_hash": "samehash",
            "message": "same",
            "timestamp": "2026-01-01T12:00:00+00:00",
            "files_changed": [],
        }
        await ls._on_git_commit(payload)
        await ls._on_git_commit(payload)
        self.assertEqual(len(ls._state["recent_changes"]), 1)


if __name__ == "__main__":
    unittest.main()
