"""Smoke tests for nutrition router auth (auth-header-migration Slice 2).

Covers REQ-1 and REQ-3 scenarios for nutrition.py:
  - valid token → success (auth endpoints)
  - missing token → 401
  - multipart endpoints authenticate via Authorization header (no Form token)
"""
from __future__ import annotations

import io
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
    """Mounts nutrition router with a valid session in web_app.sessions."""
    import importlib
    import web_app

    web_app.sessions = {
        "nutr-valid-token": {
            "role": "admin",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import nutrition as nutrition_router
    importlib.reload(nutrition_router)

    app = FastAPI()
    app.include_router(nutrition_router.router, prefix="/api")
    return TestClient(app)


class TestNutritionAuth:
    def test_profile_valid_token_returns_200(self, app_with_session):
        """GET /nutrition/profile with valid ?token= → 200."""
        mock_profile = MagicMock()
        mock_svc = MagicMock()
        mock_svc.get_profile.return_value = mock_profile

        with patch("routers.nutrition._get_service", return_value=mock_svc):
            resp = app_with_session.get(
                "/api/nutrition/profile?user_id=admin",
                headers={"Authorization": "Bearer nutr-valid-token"},
            )
        assert resp.status_code == 200

    def test_profile_missing_token_returns_401(self, app_with_session):
        """GET /nutrition/profile without token → 401."""
        resp = app_with_session.get("/api/nutrition/profile")
        assert resp.status_code == 401

    def test_daily_valid_token_returns_200(self, app_with_session):
        """GET /nutrition/daily with valid token → 200."""
        mock_summary = MagicMock()
        mock_summary.date = "2026-01-01"
        mock_summary.user_id = "admin"
        mock_summary.calories = 0
        mock_summary.protein_g = 0
        mock_summary.carbs_g = 0
        mock_summary.fats_g = 0
        mock_summary.fiber_g = 0
        mock_summary.water_ml = 0
        mock_summary.insulin_flags_count = 0
        mock_summary.targets = {}
        mock_summary.adherence_score = 0
        mock_summary.status_indicator = "ok"
        mock_summary.meals = []
        mock_svc = MagicMock()
        mock_svc.get_daily_summary.return_value = mock_summary

        with patch("routers.nutrition._get_service", return_value=mock_svc):
            resp = app_with_session.get(
                "/api/nutrition/daily?user_id=admin",
                headers={"Authorization": "Bearer nutr-valid-token"},
            )
        assert resp.status_code == 200

    def test_daily_missing_token_returns_401(self, app_with_session):
        """GET /nutrition/daily without token → 401."""
        resp = app_with_session.get("/api/nutrition/daily")
        assert resp.status_code == 401

    def test_analyze_food_multipart_url_token_returns_400_no_content(self, app_with_session):
        """POST /nutrition/food/analyze with ?token= in URL (REQ-3).

        Token is authenticated via URL query param (shim). No Form token field.
        Passes auth gate; returns 400 because no text/file provided (not 401).
        """
        resp = app_with_session.post(
            "/api/nutrition/food/analyze",
            data={},  # no 'token' form field, no text, no file
            headers={"Authorization": "Bearer nutr-valid-token"},
        )
        # 400 = auth passed, content missing. 401 would mean auth failed.
        assert resp.status_code == 400
        assert "text" in resp.json()["detail"].lower() or "imagen" in resp.json()["detail"].lower()

    def test_analyze_food_multipart_missing_token_returns_401(self, app_with_session):
        """POST /nutrition/food/analyze without any token → 401."""
        resp = app_with_session.post(
            "/api/nutrition/food/analyze",
            data={"meal_type": "other"},
        )
        assert resp.status_code == 401

    def test_log_quick_multipart_url_token_returns_400_no_content(self, app_with_session):
        """POST /nutrition/food/log-quick with ?token= in URL (REQ-3).

        No Form token field; token via URL shim. Returns 400 (no content), not 401.
        """
        resp = app_with_session.post(
            "/api/nutrition/food/log-quick",
            data={"user_id": "admin"},  # no 'token' form field
            headers={"Authorization": "Bearer nutr-valid-token"},
        )
        assert resp.status_code == 400

    def test_log_quick_multipart_missing_token_returns_401(self, app_with_session):
        """POST /nutrition/food/log-quick without token → 401."""
        resp = app_with_session.post(
            "/api/nutrition/food/log-quick",
            data={"user_id": "admin"},
        )
        assert resp.status_code == 401
