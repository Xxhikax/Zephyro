"""Smoke tests for canvas router auth (auth-header-migration Slice 1).

Covers REQ-1 scenarios for canvas.py:
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
    """Mounts canvas router with a valid session in web_app.sessions."""
    import importlib
    import web_app

    web_app.sessions = {
        "canvas-valid-token": {
            "role": "user",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import canvas as canvas_router
    importlib.reload(canvas_router)

    app = FastAPI()
    app.include_router(canvas_router.router, prefix="/api")
    return TestClient(app)


class TestCanvasAuth:
    def test_summary_valid_token_returns_200(self, app_with_session):
        resp = app_with_session.get(
            "/api/canvas/summary",
            headers={"Authorization": "Bearer canvas-valid-token"},
        )
        # Returns 200 (configured: False is fine — no Canvas module in test env)
        assert resp.status_code == 200

    def test_summary_missing_token_returns_401(self, app_with_session):
        resp = app_with_session.get("/api/canvas/summary")
        assert resp.status_code == 401

    def test_cache_clear_valid_token_returns_200(self, app_with_session):
        resp = app_with_session.post(
            "/api/canvas/cache/clear",
            headers={"Authorization": "Bearer canvas-valid-token"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_cache_clear_missing_token_returns_401(self, app_with_session):
        resp = app_with_session.post("/api/canvas/cache/clear")
        assert resp.status_code == 401
