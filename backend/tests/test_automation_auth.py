"""Smoke tests for automation router auth (auth-header-migration Slice 1).

Covers REQ-1 scenarios for automation.py:
  - valid token → 200
  - missing token → 401
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def app_with_session(monkeypatch):
    """Mounts automation router with a valid session in web_app.sessions."""
    import importlib
    import web_app

    web_app.sessions = {
        "auto-valid-token": {
            "role": "admin",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import automation as automation_router
    importlib.reload(automation_router)

    app = FastAPI()
    app.include_router(automation_router.router, prefix="/api")
    return TestClient(app)


class TestAutomationAuth:
    def test_jobs_valid_token_returns_200(self, app_with_session):
        resp = app_with_session.get("/api/automation/jobs", headers={"Authorization": "Bearer auto-valid-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "named_jobs" in data
        assert "all_jobs" in data

    def test_jobs_missing_token_returns_401(self, app_with_session):
        resp = app_with_session.get("/api/automation/jobs")
        assert resp.status_code == 401

    def test_trigger_valid_token_unknown_job_returns_404(self, app_with_session):
        # 404 means auth passed (job not found), not 401
        resp = app_with_session.post(
            "/api/automation/trigger/nonexistent_job",
            headers={"Authorization": "Bearer auto-valid-token"},
        )
        assert resp.status_code == 404

    def test_trigger_missing_token_returns_401(self, app_with_session):
        resp = app_with_session.post("/api/automation/trigger/some_job")
        assert resp.status_code == 401
