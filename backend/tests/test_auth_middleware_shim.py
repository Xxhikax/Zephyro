"""Unit tests for auth_middleware token resolution (Phase 3 — ?token= shim removed).

Covers:
  - Token resolves via Authorization: Bearer header
  - Token resolves via X-Token header
  - ?token= query param NO LONGER authenticates (shim removed in Phase 3)
  - 401 when no token header is present
  - Invalid token returns 401
  - A stray ?token= in the URL is ignored; header auth still wins
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI, Depends

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def app_with_session(monkeypatch):
    """Minimal app that has one protected endpoint, with sessions mocked."""
    import web_app
    web_app.sessions = {
        "shim-test-token": {
            "role": "admin",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from core.auth_middleware import require_auth

    app = FastAPI()

    @app.get("/protected")
    def protected(session: dict = Depends(require_auth)):
        return {"ok": True, "role": session.get("role")}

    return TestClient(app)


class TestGetTokenFromHeader:
    def test_resolves_via_bearer_header(self, app_with_session):
        resp = app_with_session.get(
            "/protected",
            headers={"Authorization": "Bearer shim-test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_resolves_via_x_token_header(self, app_with_session):
        resp = app_with_session.get(
            "/protected",
            headers={"X-Token": "shim-test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_query_param_no_longer_authenticates(self, app_with_session):
        # Phase 3: the ?token= shim was removed. A valid token in the query
        # string must NOT authenticate — only headers are honored.
        resp = app_with_session.get("/protected?token=shim-test-token")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Token requerido"

    def test_raises_401_when_all_absent(self, app_with_session):
        resp = app_with_session.get("/protected")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Token requerido"

    def test_invalid_token_returns_401(self, app_with_session):
        # A bad token in the Authorization header resolves but fails session lookup.
        resp = app_with_session.get(
            "/protected",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Sesión inválida"

    def test_bearer_ignores_stray_query_param(self, app_with_session):
        # A leftover ?token= in the URL must be ignored; the Bearer header wins.
        resp = app_with_session.get(
            "/protected?token=bad-token",
            headers={"Authorization": "Bearer shim-test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_x_token_ignores_stray_query_param(self, app_with_session):
        resp = app_with_session.get(
            "/protected?token=bad-token",
            headers={"X-Token": "shim-test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
