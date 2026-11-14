"""Smoke tests for routers/auth.py — auth migration (Slice A).

Covers:
  /auth/identify — migrated to Depends(require_auth):
    - valid token → 200
    - missing token → 401
  /auth/logout — SOFT/idempotent (NOT Depends; must never 401):
    - with valid token → 200, success:true
    - with invalid token → 200, success:false
    - WITHOUT token → 200, success:false (idempotency preservation, NOT 401)

Fully mocked: web_app.sessions pre-populated; NO real browser/CDP.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VALID_TOKEN = "valid-auth-router-token"
ADMIN_TOKEN = "admin-auth-router-token"


def _make_sessions():
    return {
        VALID_TOKEN: {
            "role": "guest",
            "name": None,
            "authenticated": False,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        },
        ADMIN_TOKEN: {
            "role": "admin",
            "name": "Bruno",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        },
    }


@pytest.fixture
def client():
    import importlib
    import web_app

    web_app.sessions = _make_sessions()

    from fastapi import FastAPI
    import routers.auth as auth_mod
    importlib.reload(auth_mod)

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api")
    return TestClient(app)


class TestAuthIdentify:
    """/auth/identify is gated by Depends(require_auth)."""

    def test_identify_valid_token(self, client):
        resp = client.post("/api/auth/identify", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_identify_missing_token(self, client):
        resp = client.post("/api/auth/identify")
        assert resp.status_code == 401

    def test_identify_invalid_token(self, client):
        resp = client.post("/api/auth/identify", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401

    def test_identify_sets_name_for_guest(self, client):
        resp = client.post("/api/auth/identify", headers={"Authorization": f"Bearer {VALID_TOKEN}"}, params={"name": "TestUser"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "TestUser" in body["message"]


class TestAuthLogout:
    """/auth/logout MUST be soft — never raises 401, regardless of token presence."""

    def test_logout_with_valid_token_returns_success(self, client):
        resp = client.post(f"/api/auth/logout?token={ADMIN_TOKEN}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    def test_logout_with_invalid_token_returns_200_not_401(self, client):
        # Idempotency: unknown token must NOT produce 401
        resp = client.post("/api/auth/logout?token=unknown-garbage-token")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False  # session not found, but no error code

    def test_logout_without_token_returns_200_not_401(self, client):
        # Critical: missing token must return 2xx (success:false), NEVER 401
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "401" not in str(resp.status_code)
