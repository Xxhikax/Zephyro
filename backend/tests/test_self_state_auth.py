"""Smoke tests for routers/self_state.py — auth migration (Slice A).

Verifies Depends(require_admin) is correctly enforced on all 7 admin endpoints:
  - valid admin token  → 200
  - missing token      → 401
  - non-admin token    → 403

Fully mocked: web_app.sessions is pre-populated; NO real orchestrator, browser, or CDP.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN_TOKEN = "admin-slice-a-test-token"
GUEST_TOKEN = "guest-slice-a-test-token"


def _make_sessions():
    return {
        ADMIN_TOKEN: {
            "role": "admin",
            "name": "Bruno",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        },
        GUEST_TOKEN: {
            "role": "guest",
            "name": "Invitado",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        },
    }


def _mock_orchestrator() -> MagicMock:
    orc = MagicMock()
    orc.self_state.export_state.return_value = {"alive": True}
    orc.self_state.get_bug_reports_summary.return_value = []
    orc.self_state.capabilities = {}
    orc.self_state.session_stats = {"interactions_count": 0}
    orc.self_state.clear_bug_reports.return_value = None
    orc.consciousness.get_snapshot.return_value = {"health": "ok"}
    orc.consciousness.introspect.return_value = {"issues": []}
    orc.skill_registry.get_all_skills.return_value = []
    orc._handle_reflect_command = AsyncMock(return_value="reflexión completada")
    return orc


@pytest.fixture
def client():
    import importlib
    import web_app

    web_app.sessions = _make_sessions()

    from fastapi import FastAPI
    import routers.self_state as self_state_mod
    importlib.reload(self_state_mod)

    app = FastAPI()
    orc = _mock_orchestrator()
    app.state.orchestrator = orc
    app.include_router(self_state_mod.router, prefix="/api")
    return TestClient(app)


class TestSelfStateAdminRequired:
    """All self_state endpoints require admin role via Depends(require_admin)."""

    # --- /self/state ---
    def test_state_valid_admin(self, client):
        resp = client.get("/api/self/state", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_state_missing_token(self, client):
        resp = client.get("/api/self/state")
        assert resp.status_code == 401

    def test_state_non_admin(self, client):
        resp = client.get("/api/self/state", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # --- /self/bugs ---
    def test_bugs_valid_admin(self, client):
        resp = client.get("/api/self/bugs", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_bugs_missing_token(self, client):
        resp = client.get("/api/self/bugs")
        assert resp.status_code == 401

    def test_bugs_non_admin(self, client):
        resp = client.get("/api/self/bugs", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # --- /self/reflect ---
    def test_reflect_missing_token(self, client):
        resp = client.post("/api/self/reflect")
        assert resp.status_code == 401

    def test_reflect_non_admin(self, client):
        resp = client.post("/api/self/reflect", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    def test_reflect_valid_admin(self, client):
        resp = client.post("/api/self/reflect", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    # --- /self/bugs/clear ---
    def test_bugs_clear_missing_token(self, client):
        resp = client.delete("/api/self/bugs/clear")
        assert resp.status_code == 401

    def test_bugs_clear_non_admin(self, client):
        resp = client.delete("/api/self/bugs/clear", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    def test_bugs_clear_valid_admin(self, client):
        resp = client.delete("/api/self/bugs/clear", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    # --- /self/living ---
    def test_living_missing_token(self, client):
        resp = client.get("/api/self/living")
        assert resp.status_code == 401

    def test_living_non_admin(self, client):
        resp = client.get("/api/self/living", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    def test_living_valid_admin(self, client):
        resp = client.get("/api/self/living", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    # --- /self/introspect ---
    def test_introspect_missing_token(self, client):
        resp = client.get("/api/self/introspect")
        assert resp.status_code == 401

    def test_introspect_non_admin(self, client):
        resp = client.get("/api/self/introspect", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    def test_introspect_valid_admin(self, client):
        resp = client.get("/api/self/introspect", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    # --- /self/capacidades ---
    def test_capacidades_missing_token(self, client):
        resp = client.get("/api/self/capacidades")
        assert resp.status_code == 401

    def test_capacidades_non_admin(self, client):
        resp = client.get("/api/self/capacidades", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    def test_capacidades_valid_admin(self, client):
        resp = client.get("/api/self/capacidades", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
