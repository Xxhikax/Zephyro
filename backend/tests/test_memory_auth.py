"""Smoke tests for memory router auth (auth-header-migration Slice 3).

Covers REQ-1 scenarios for memory.py:
  - admin endpoints (history/*): valid admin → not 401/403; missing → 401; non-admin → 403
  - auth endpoints (session/reset, kg/*, reminders/*): valid token → not 401; missing → 401
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def app_with_admin_session(monkeypatch):
    """Mounts memory router with admin session."""
    import importlib
    import web_app

    web_app.sessions = {
        "mem-admin-token": {
            "role": "admin",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import memory as memory_router
    importlib.reload(memory_router)

    app = FastAPI()

    # Mount with a mock orchestrator on app state
    mock_orc = MagicMock()
    mock_orc.history = MagicMock()
    mock_orc.history.get_grouped_conversations.return_value = []
    mock_orc.knowledge_graph = MagicMock()
    mock_orc.knowledge_graph.get_stats.return_value = {}
    mock_orc.reminder_system = MagicMock()
    mock_orc.reminder_system.list_reminders.return_value = []
    mock_orc.reset_conversation = MagicMock()
    app.state.orchestrator = mock_orc

    app.include_router(memory_router.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def app_with_user_session(monkeypatch):
    """Mounts memory router with non-admin session."""
    import importlib
    import web_app

    web_app.sessions = {
        "mem-user-token": {
            "role": "user",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import memory as memory_router
    importlib.reload(memory_router)

    app = FastAPI()

    mock_orc = MagicMock()
    mock_orc.knowledge_graph = MagicMock()
    mock_orc.reminder_system = MagicMock()
    mock_orc.reminder_system.list_reminders.return_value = []
    mock_orc.reset_conversation = MagicMock()
    app.state.orchestrator = mock_orc

    app.include_router(memory_router.router, prefix="/api")
    return TestClient(app)


class TestMemoryAdminEndpoints:
    """history/* endpoints require admin."""

    def test_history_list_admin_token_not_401_403(self, app_with_admin_session):
        resp = app_with_admin_session.get(
            "/api/history/list",
            headers={"Authorization": "Bearer mem-admin-token"},
        )
        assert resp.status_code not in (401, 403)

    def test_history_list_missing_token_returns_401(self, app_with_admin_session):
        resp = app_with_admin_session.get("/api/history/list")
        assert resp.status_code == 401

    def test_history_list_non_admin_returns_403(self, app_with_user_session):
        resp = app_with_user_session.get(
            "/api/history/list",
            headers={"Authorization": "Bearer mem-user-token"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Se requiere rol admin"

    def test_history_statistics_admin_token_not_401_403(self, app_with_admin_session):
        resp = app_with_admin_session.get(
            "/api/history/statistics",
            headers={"Authorization": "Bearer mem-admin-token"},
        )
        assert resp.status_code not in (401, 403)

    def test_history_statistics_missing_token_returns_401(self, app_with_admin_session):
        resp = app_with_admin_session.get("/api/history/statistics")
        assert resp.status_code == 401

    def test_history_statistics_non_admin_returns_403(self, app_with_user_session):
        resp = app_with_user_session.get(
            "/api/history/statistics",
            headers={"Authorization": "Bearer mem-user-token"},
        )
        assert resp.status_code == 403

    def test_history_clear_admin_token_not_401_403(self, app_with_admin_session):
        resp = app_with_admin_session.delete(
            "/api/history/clear",
            headers={"Authorization": "Bearer mem-admin-token"},
        )
        assert resp.status_code not in (401, 403)

    def test_history_clear_non_admin_returns_403(self, app_with_user_session):
        resp = app_with_user_session.delete(
            "/api/history/clear",
            headers={"Authorization": "Bearer mem-user-token"},
        )
        assert resp.status_code == 403


class TestMemoryAuthEndpoints:
    """session/reset, kg/*, reminders/* require auth (not admin)."""

    def test_session_reset_valid_token_not_401(self, app_with_user_session):
        resp = app_with_user_session.post(
            "/api/session/reset",
            headers={"Authorization": "Bearer mem-user-token"},
        )
        assert resp.status_code != 401

    def test_session_reset_missing_token_returns_401(self, app_with_user_session):
        resp = app_with_user_session.post("/api/session/reset")
        assert resp.status_code == 401

    def test_kg_stats_valid_token_not_401(self, app_with_user_session):
        resp = app_with_user_session.get(
            "/api/kg/stats",
            headers={"Authorization": "Bearer mem-user-token"},
        )
        assert resp.status_code != 401

    def test_kg_stats_missing_token_returns_401(self, app_with_user_session):
        resp = app_with_user_session.get("/api/kg/stats")
        assert resp.status_code == 401

    def test_reminders_list_valid_token_not_401(self, app_with_user_session):
        resp = app_with_user_session.get(
            "/api/reminders/list",
            headers={"Authorization": "Bearer mem-user-token"},
        )
        assert resp.status_code != 401

    def test_reminders_list_missing_token_returns_401(self, app_with_user_session):
        resp = app_with_user_session.get("/api/reminders/list")
        assert resp.status_code == 401
