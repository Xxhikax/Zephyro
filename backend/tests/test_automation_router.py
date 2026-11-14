"""Tests para routers/automation.py (trigger_now via REST)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def app_with_session(monkeypatch):
    """Mounta el automation router en una app FastAPI minimal con sesión válida."""
    import importlib
    # Mockeamos web_app.sessions sin tocar el lifespan completo
    import web_app
    web_app.sessions = {
        "valid-token": {
            "role": "admin",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from fastapi import FastAPI
    from routers import automation as automation_router
    importlib.reload(automation_router)

    app = FastAPI()
    app.include_router(automation_router.router, prefix="/api")
    return TestClient(app)


class TestAutomationRouter:
    def test_jobs_requires_auth(self, app_with_session):
        resp = app_with_session.get("/api/automation/jobs", headers={"Authorization": "Bearer invalid"})
        assert resp.status_code == 401

    def test_jobs_lists_when_authed(self, app_with_session):
        resp = app_with_session.get("/api/automation/jobs", headers={"Authorization": "Bearer valid-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "named_jobs" in data
        assert "all_jobs" in data

    def test_trigger_unknown_job_returns_404(self, app_with_session):
        resp = app_with_session.post(
            "/api/automation/trigger/this_job_does_not_exist",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert resp.status_code == 404

    def test_trigger_registered_job_returns_ok(self, app_with_session):
        from core.scheduler import get_scheduler
        sched = get_scheduler()
        called = []
        sched.register_named_job(
            "test_router_job", lambda: called.append(1),
            trigger="cron", hour=0,
        )
        resp = app_with_session.post(
            "/api/automation/trigger/test_router_job",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ran"] is True
        assert body["job"] == "test_router_job"
