"""Smoke tests for chat router HTTP endpoints auth (auth-header-migration Slice 3).

Covers REQ-1, D3 scenarios for chat.py HTTP endpoints ONLY:
  - /chat/send: valid token → not 401; missing → 401; raw token still usable as session_id
  - /feedback: valid token → not 401; missing → 401
  - /learning/stats: admin → not 401/403; non-admin → 403; missing → 401
  - /ws endpoint: completely untouched (not tested here)
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
def app_with_user_session(monkeypatch):
    """Mounts chat router HTTP endpoints with a non-admin session. WS not tested."""
    import importlib
    import web_app

    web_app.sessions = {
        "chat-user-token": {
            "role": "user",
            "authenticated": True,
            "name": "TestUser",
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import chat as chat_router
    importlib.reload(chat_router)

    app = FastAPI()

    mock_orc = MagicMock()
    mock_orc.handle_input = AsyncMock(return_value="mocked response")
    mock_orc._last_interaction_id = 42
    mock_orc.feedback_system = MagicMock()
    mock_orc.feedback_system.submit_feedback.return_value = True
    app.state.orchestrator = mock_orc

    # Include only non-WS routes by mounting the router — WS route is included
    # but will not be exercised by TestClient HTTP calls
    app.include_router(chat_router.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def app_with_admin_session(monkeypatch):
    """Mounts chat router with an admin session."""
    import importlib
    import web_app

    web_app.sessions = {
        "chat-admin-token": {
            "role": "admin",
            "authenticated": True,
            "name": "AdminUser",
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import chat as chat_router
    importlib.reload(chat_router)

    app = FastAPI()

    mock_orc = MagicMock()
    mock_orc.feedback_system = MagicMock()
    mock_orc.feedback_system.get_stats.return_value = {}
    mock_orc.few_shot_selector = MagicMock()
    mock_orc.few_shot_selector.count.return_value = 0
    mock_orc.finetune_manager = None
    mock_orc.reflection_engine = None
    app.state.orchestrator = mock_orc

    app.include_router(chat_router.router, prefix="/api")
    return TestClient(app)


class TestChatSendAuth:
    def test_send_valid_token_not_401(self, app_with_user_session):
        """POST /chat/send with valid token → not 401 (D3: dual-Depends)."""
        resp = app_with_user_session.post(
            "/api/chat/send",
            headers={"Authorization": "Bearer chat-user-token"},
            json={"message": "Hola"},
        )
        assert resp.status_code != 401

    def test_send_missing_token_returns_401(self, app_with_user_session):
        """POST /chat/send without token → 401."""
        resp = app_with_user_session.post(
            "/api/chat/send",
            json={"message": "Hola"},
        )
        assert resp.status_code == 401

    def test_send_token_used_as_session_id(self, app_with_user_session):
        """Confirm raw token is forwarded as session_id in RequestContext (D3)."""
        from unittest.mock import patch
        captured_ctx = {}

        from core import request_context

        original_cls = request_context.RequestContext

        def capturing_ctx(**kwargs):
            captured_ctx.update(kwargs)
            return original_cls(**kwargs)

        with patch("routers.chat.RequestContext", side_effect=capturing_ctx):
            resp = app_with_user_session.post(
                "/api/chat/send",
                headers={"Authorization": "Bearer chat-user-token"},
                json={"message": "Hola"},
            )
        # session_id must be the raw token string
        assert captured_ctx.get("session_id") == "chat-user-token"


class TestChatFeedbackAuth:
    def test_feedback_valid_token_not_401(self, app_with_user_session):
        """POST /feedback with valid token → not 401."""
        resp = app_with_user_session.post(
            "/api/feedback",
            headers={"Authorization": "Bearer chat-user-token"},
            json={"interaction_id": 1, "score": 1},
        )
        assert resp.status_code != 401

    def test_feedback_missing_token_returns_401(self, app_with_user_session):
        """POST /feedback without token → 401."""
        resp = app_with_user_session.post(
            "/api/feedback",
            json={"interaction_id": 1, "score": 1},
        )
        assert resp.status_code == 401


class TestChatLearningStatsAuth:
    def test_learning_stats_admin_not_401_403(self, app_with_admin_session):
        """GET /learning/stats with admin token → not 401/403."""
        resp = app_with_admin_session.get(
            "/api/learning/stats",
            headers={"Authorization": "Bearer chat-admin-token"},
        )
        assert resp.status_code not in (401, 403)

    def test_learning_stats_missing_token_returns_401(self, app_with_admin_session):
        """GET /learning/stats without token → 401."""
        resp = app_with_admin_session.get("/api/learning/stats")
        assert resp.status_code == 401

    def test_learning_stats_non_admin_returns_403(self, app_with_user_session):
        """GET /learning/stats with non-admin token → 403."""
        resp = app_with_user_session.get(
            "/api/learning/stats",
            headers={"Authorization": "Bearer chat-user-token"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Se requiere rol admin"
