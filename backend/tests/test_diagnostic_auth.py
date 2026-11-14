"""Smoke tests for diagnostic router auth (auth-header-migration Slice 1).

Covers REQ-1 admin scenarios for diagnostic.py:
  - valid admin token → endpoint reached (200 or 503 if gate not init)
  - missing token → 401
  - non-admin token → 403
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def app_with_sessions(monkeypatch):
    """Mounts diagnostic router with admin and non-admin sessions mocked."""
    import importlib
    import web_app

    web_app.sessions = {
        "diag-admin-token": {
            "role": "admin",
            "authenticated": True,
            "user_id": "test_admin",
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        },
        "diag-user-token": {
            "role": "user",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        },
    }

    from routers import diagnostic as diag_router
    importlib.reload(diag_router)

    app = FastAPI()
    app.include_router(diag_router.router, prefix="/api")
    return TestClient(app)


class TestDiagnosticAuth:
    # --- /self/diagnose POST ---

    def test_diagnose_missing_token_returns_401(self, app_with_sessions):
        resp = app_with_sessions.post("/api/self/diagnose")
        assert resp.status_code == 401

    def test_diagnose_non_admin_returns_403(self, app_with_sessions):
        resp = app_with_sessions.post(
            "/api/self/diagnose",
            headers={"Authorization": "Bearer diag-user-token"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Se requiere rol admin"

    def test_diagnose_admin_token_passes_auth(self, app_with_sessions):
        # Gate is not initialized → 503, but auth passed (not 401/403)
        resp = app_with_sessions.post(
            "/api/self/diagnose",
            headers={"Authorization": "Bearer diag-admin-token"},
        )
        assert resp.status_code in (200, 503)

    # --- /self/diagnose/pending GET ---

    def test_pending_missing_token_returns_401(self, app_with_sessions):
        resp = app_with_sessions.get("/api/self/diagnose/pending")
        assert resp.status_code == 401

    def test_pending_non_admin_returns_403(self, app_with_sessions):
        resp = app_with_sessions.get(
            "/api/self/diagnose/pending",
            headers={"Authorization": "Bearer diag-user-token"},
        )
        assert resp.status_code == 403

    def test_pending_admin_token_passes_auth(self, app_with_sessions):
        resp = app_with_sessions.get(
            "/api/self/diagnose/pending",
            headers={"Authorization": "Bearer diag-admin-token"},
        )
        # Gate absent → returns {"success": True, "pending": []}
        assert resp.status_code == 200

    # --- /self/diagnose/{id}/cancel POST ---

    def test_cancel_missing_token_returns_401(self, app_with_sessions):
        resp = app_with_sessions.post("/api/self/diagnose/req-123/cancel")
        assert resp.status_code == 401

    def test_cancel_non_admin_returns_403(self, app_with_sessions):
        resp = app_with_sessions.post(
            "/api/self/diagnose/req-123/cancel",
            headers={"Authorization": "Bearer diag-user-token"},
        )
        assert resp.status_code == 403

    def test_cancel_admin_token_passes_auth(self, app_with_sessions):
        resp = app_with_sessions.post(
            "/api/self/diagnose/req-123/cancel",
            headers={"Authorization": "Bearer diag-admin-token"},
        )
        assert resp.status_code in (200, 503)
