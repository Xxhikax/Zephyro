"""Smoke tests for fitness router auth (auth-header-migration Slice 2).

Covers REQ-1 and REQ-3 scenarios for fitness.py:
  - valid token → success
  - missing token → 401
  - multipart /fitness/session/verify authenticates via Authorization header (no Form token)
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def app_with_session(monkeypatch):
    """Mounts fitness router with a valid session in web_app.sessions."""
    import importlib
    import web_app

    web_app.sessions = {
        "fit-valid-token": {
            "role": "admin",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import fitness as fitness_router
    importlib.reload(fitness_router)

    app = FastAPI()
    app.include_router(fitness_router.router, prefix="/api")
    return TestClient(app)


class TestFitnessAuth:
    def test_stats_valid_token_returns_200(self, app_with_session):
        """GET /fitness/stats with valid token → 200."""
        mock_svc = MagicMock()
        mock_svc.get_streak.return_value = {}
        mock_svc.get_weekly_summary.return_value = {}
        mock_svc.get_profile.return_value = {}

        with patch("routers.fitness._get_service", return_value=mock_svc):
            resp = app_with_session.get(
                "/api/fitness/stats",
                headers={"Authorization": "Bearer fit-valid-token"},
            )
        assert resp.status_code == 200

    def test_stats_missing_token_returns_401(self, app_with_session):
        """GET /fitness/stats without token → 401."""
        resp = app_with_session.get("/api/fitness/stats")
        assert resp.status_code == 401

    def test_plan_current_valid_token_returns_200(self, app_with_session):
        """GET /fitness/plan/current with valid token → 200."""
        mock_svc = MagicMock()
        mock_svc.get_active_plan.return_value = None

        with patch("routers.fitness._get_service", return_value=mock_svc):
            resp = app_with_session.get(
                "/api/fitness/plan/current",
                headers={"Authorization": "Bearer fit-valid-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["plan"] is None

    def test_plan_current_missing_token_returns_401(self, app_with_session):
        """GET /fitness/plan/current without token → 401."""
        resp = app_with_session.get("/api/fitness/plan/current")
        assert resp.status_code == 401

    def test_verify_session_multipart_url_token_authenticates(self, app_with_session):
        """POST /fitness/session/verify with ?token= in URL and no Form token (REQ-3).

        Auth passes via URL shim. Service call may fail (no real svc), but not 401.
        We mock the service so we get a clean 200.
        """
        mock_svc = MagicMock()
        # verify_session returns (score, counts)
        import asyncio

        async def _mock_verify(**kwargs):
            return (0.95, True)

        mock_svc.verify_session = _mock_verify
        mock_svc.log_session.return_value = {"ok": True}

        with patch("routers.fitness._get_service", return_value=mock_svc):
            resp = app_with_session.post(
                "/api/fitness/session/verify",
                data={"session_id": "1", "message": "done"},
                headers={"Authorization": "Bearer fit-valid-token"},
                # no 'token' form field
            )
        assert resp.status_code == 200

    def test_verify_session_multipart_missing_token_returns_401(self, app_with_session):
        """POST /fitness/session/verify without any token → 401."""
        resp = app_with_session.post(
            "/api/fitness/session/verify",
            data={"session_id": "1", "message": "done"},
        )
        assert resp.status_code == 401

    def test_alternatives_valid_token_returns_200(self, app_with_session):
        """GET /fitness/session/{id}/alternatives with valid token → 200."""
        mock_svc = MagicMock()
        mock_svc.suggest_alternative_slots.return_value = []

        with patch("routers.fitness._get_service", return_value=mock_svc):
            resp = app_with_session.get(
                "/api/fitness/session/1/alternatives",
                headers={"Authorization": "Bearer fit-valid-token"},
            )
        assert resp.status_code == 200

    def test_alternatives_missing_token_returns_401(self, app_with_session):
        """GET /fitness/session/{id}/alternatives without token → 401."""
        resp = app_with_session.get("/api/fitness/session/1/alternatives")
        assert resp.status_code == 401
