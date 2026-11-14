"""Smoke tests for code router auth (auth-header-migration Slice 3).

Covers REQ-1 admin scenarios for code.py:
  - valid admin token → endpoint reached (not 401/403)
  - missing token → 401
  - non-admin token → 403 (for all endpoints including former soft-admin /cache/*)
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
    """Mounts code router with admin session."""
    import importlib
    import web_app

    web_app.sessions = {
        "code-admin-token": {
            "role": "admin",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import code as code_router
    importlib.reload(code_router)

    app = FastAPI()

    mock_orc = MagicMock()
    mock_orc.get_gitnexus_health = AsyncMock(return_value={"status": "ok"})
    mock_orc.llm_cache = MagicMock()
    mock_orc.llm_cache.stats = {"hits": 0, "misses": 0}
    mock_orc.llm_cache.clear = MagicMock(return_value=0)
    app.state.orchestrator = mock_orc

    app.include_router(code_router.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def app_with_user_session(monkeypatch):
    """Mounts code router with non-admin session."""
    import importlib
    import web_app

    web_app.sessions = {
        "code-user-token": {
            "role": "user",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import code as code_router
    importlib.reload(code_router)

    app = FastAPI()
    mock_orc = MagicMock()
    mock_orc.llm_cache = MagicMock()
    mock_orc.llm_cache.stats = {}
    app.state.orchestrator = mock_orc

    app.include_router(code_router.router, prefix="/api")
    return TestClient(app)


class TestCodeAdminEndpoints:
    def test_code_health_admin_not_401_403(self, app_with_admin_session):
        """GET /code/health with admin token → not 401/403."""
        resp = app_with_admin_session.get(
            "/api/code/health",
            headers={"Authorization": "Bearer code-admin-token"},
        )
        assert resp.status_code not in (401, 403)

    def test_code_health_missing_token_returns_401(self, app_with_admin_session):
        resp = app_with_admin_session.get("/api/code/health")
        assert resp.status_code == 401

    def test_code_health_non_admin_returns_403(self, app_with_user_session):
        resp = app_with_user_session.get(
            "/api/code/health",
            headers={"Authorization": "Bearer code-user-token"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Se requiere rol admin"

    def test_cache_stats_admin_not_401_403(self, app_with_admin_session):
        """GET /cache/stats (former soft-admin) now requires admin."""
        resp = app_with_admin_session.get(
            "/api/cache/stats",
            headers={"Authorization": "Bearer code-admin-token"},
        )
        assert resp.status_code not in (401, 403)

    def test_cache_stats_missing_token_returns_401(self, app_with_admin_session):
        resp = app_with_admin_session.get("/api/cache/stats")
        assert resp.status_code == 401

    def test_cache_stats_non_admin_returns_403(self, app_with_user_session):
        """Non-admin now gets 403 for /cache/stats (was previously 403 via _validate_admin)."""
        resp = app_with_user_session.get(
            "/api/cache/stats",
            headers={"Authorization": "Bearer code-user-token"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Se requiere rol admin"

    def test_cache_clear_admin_not_401_403(self, app_with_admin_session):
        """DELETE /cache/clear (former soft-admin) now requires admin."""
        resp = app_with_admin_session.delete(
            "/api/cache/clear",
            headers={"Authorization": "Bearer code-admin-token"},
        )
        assert resp.status_code not in (401, 403)

    def test_cache_clear_missing_token_returns_401(self, app_with_admin_session):
        resp = app_with_admin_session.delete("/api/cache/clear")
        assert resp.status_code == 401

    def test_cache_clear_non_admin_returns_403(self, app_with_user_session):
        resp = app_with_user_session.delete(
            "/api/cache/clear",
            headers={"Authorization": "Bearer code-user-token"},
        )
        assert resp.status_code == 403
