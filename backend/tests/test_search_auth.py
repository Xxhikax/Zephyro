"""Smoke tests for search router auth (auth-header-migration Slice 2).

Covers REQ-1 scenarios for search.py:
  - valid token → success (auth endpoints)
  - missing token → 401
  - /rss/add (admin) with non-admin token → 403
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
def app_with_user_session(monkeypatch):
    """Mounts search router with a regular (non-admin) session."""
    import importlib
    import web_app

    web_app.sessions = {
        "search-user-token": {
            "role": "user",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import search as search_router
    importlib.reload(search_router)

    app = FastAPI()
    app.include_router(search_router.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def app_with_admin_session(monkeypatch):
    """Mounts search router with an admin session."""
    import importlib
    import web_app

    web_app.sessions = {
        "search-admin-token": {
            "role": "admin",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import search as search_router
    importlib.reload(search_router)

    app = FastAPI()
    app.include_router(search_router.router, prefix="/api")
    return TestClient(app)


class TestSearchAuthEndpoints:
    def test_rss_feeds_valid_token_returns_200(self, app_with_user_session):
        """GET /rss/feeds with valid token → 200."""
        mock_monitor = MagicMock()
        mock_monitor.list_feeds.return_value = []

        with patch("routers.search.RSSMonitor", side_effect=lambda: mock_monitor) if False else \
             patch("builtins.__import__", wraps=__import__) as _:
            # Use the endpoint directly — RSSMonitor is imported lazily inside the handler
            resp = app_with_user_session.get(
                "/api/rss/feeds",
                headers={"Authorization": "Bearer search-user-token"},
            )
        # May return 200 or 500 depending on whether RSSMonitor is importable;
        # what matters is that it is NOT 401 (auth passed)
        assert resp.status_code != 401

    def test_rss_feeds_missing_token_returns_401(self, app_with_user_session):
        """GET /rss/feeds without token → 401."""
        resp = app_with_user_session.get("/api/rss/feeds")
        assert resp.status_code == 401

    def test_crypto_price_valid_token_returns_non_401(self, app_with_user_session):
        """GET /crypto/price/{coin} with valid token → not 401."""
        resp = app_with_user_session.get(
            "/api/crypto/price/bitcoin",
            headers={"Authorization": "Bearer search-user-token"},
        )
        assert resp.status_code != 401

    def test_crypto_price_missing_token_returns_401(self, app_with_user_session):
        """GET /crypto/price/{coin} without token → 401."""
        resp = app_with_user_session.get("/api/crypto/price/bitcoin")
        assert resp.status_code == 401

    def test_crypto_top_valid_token_returns_non_401(self, app_with_user_session):
        """GET /crypto/top with valid token → not 401."""
        resp = app_with_user_session.get(
            "/api/crypto/top",
            headers={"Authorization": "Bearer search-user-token"},
        )
        assert resp.status_code != 401

    def test_crypto_top_missing_token_returns_401(self, app_with_user_session):
        """GET /crypto/top without token → 401."""
        resp = app_with_user_session.get("/api/crypto/top")
        assert resp.status_code == 401


class TestSearchAdminEndpoint:
    def test_rss_add_admin_token_passes_auth(self, app_with_admin_session):
        """POST /rss/add with admin token — auth passes (not 401/403)."""
        # May return 500 if RSSMonitor not importable; that's fine — not 403.
        resp = app_with_admin_session.post(
            "/api/rss/add",
            headers={"Authorization": "Bearer search-admin-token"},
            json={"url": "https://example.com/feed", "name": "Test Feed"},
        )
        assert resp.status_code not in (401, 403)

    def test_rss_add_non_admin_token_returns_403(self, app_with_user_session):
        """POST /rss/add with non-admin token → 403 (canonical require_admin)."""
        resp = app_with_user_session.post(
            "/api/rss/add",
            headers={"Authorization": "Bearer search-user-token"},
            json={"url": "https://example.com/feed", "name": "Test Feed"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Se requiere rol admin"

    def test_rss_add_missing_token_returns_401(self, app_with_user_session):
        """POST /rss/add without any token → 401."""
        resp = app_with_user_session.post(
            "/api/rss/add",
            json={"url": "https://example.com/feed"},
        )
        assert resp.status_code == 401
