"""Smoke tests for schedule router auth (auth-header-migration Slice 3).

Covers REQ-1 and REQ-4 scenarios for schedule.py:
  - valid token → endpoint reached (not 401)
  - missing token → 401
  - user_id/role derivation: session.get("role", "guest") default preserved
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
    """Mounts schedule router with a valid user session."""
    import importlib
    import web_app

    web_app.sessions = {
        "sched-valid-token": {
            "role": "user",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import schedule as schedule_router
    importlib.reload(schedule_router)

    app = FastAPI()
    app.include_router(schedule_router.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def app_with_guest_session(monkeypatch):
    """Mounts schedule router with a session that has no role key (tests default guest)."""
    import importlib
    import web_app

    web_app.sessions = {
        "sched-guest-token": {
            # role key intentionally absent — should default to "guest"
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
    }

    from routers import schedule as schedule_router
    importlib.reload(schedule_router)

    app = FastAPI()
    app.include_router(schedule_router.router, prefix="/api")
    return TestClient(app)


class TestScheduleAuth:
    def test_list_events_valid_token_not_401(self, app_with_session):
        """GET /schedule/events with valid token → not 401."""
        # ScheduleService may fail in test env — what matters is auth passes
        with patch("routers.schedule.get_schedule_service") as mock_svc:
            svc = MagicMock()
            svc.get_events_in_range.return_value = []
            mock_svc.return_value = svc
            resp = app_with_session.get(
                "/api/schedule/events",
                headers={"Authorization": "Bearer sched-valid-token"},
            )
        assert resp.status_code != 401

    def test_list_events_missing_token_returns_401(self, app_with_session):
        """GET /schedule/events without token → 401."""
        resp = app_with_session.get("/api/schedule/events")
        assert resp.status_code == 401

    def test_create_event_valid_token_not_401(self, app_with_session):
        """POST /schedule/events with valid token → not 401."""
        with patch("routers.schedule.get_schedule_service") as mock_svc:
            svc = MagicMock()
            event = MagicMock()
            event.to_dict.return_value = {"id": 1, "title": "Test"}
            svc.create_event.return_value = event
            mock_svc.return_value = svc
            resp = app_with_session.post(
                "/api/schedule/events",
                json={
                    "title": "Test Event",
                    "start_iso": "2026-06-25T10:00:00Z",
                    "end_iso": "2026-06-25T11:00:00Z",
                },
                headers={"Authorization": "Bearer sched-valid-token"},
            )
        assert resp.status_code != 401

    def test_create_event_missing_token_returns_401(self, app_with_session):
        """POST /schedule/events without token → 401."""
        resp = app_with_session.post(
            "/api/schedule/events",
            json={"title": "Test", "start_iso": "2026-06-25T10:00:00Z"},
        )
        assert resp.status_code == 401

    def test_focus_start_valid_token_not_401(self, app_with_session):
        """POST /focus/start with valid token → not 401."""
        with patch("routers.schedule.get_focus_monitor") as mock_fm:
            fm = MagicMock()
            fm.start.return_value = "Focus started"
            mock_fm.return_value = fm
            resp = app_with_session.post(
                "/api/focus/start",
                headers={"Authorization": "Bearer sched-valid-token"},
            )
        assert resp.status_code != 401

    def test_focus_start_missing_token_returns_401(self, app_with_session):
        """POST /focus/start without token → 401."""
        resp = app_with_session.post("/api/focus/start")
        assert resp.status_code == 401

    def test_pomodoro_stats_valid_token_not_401(self, app_with_session):
        """GET /pomodoro/stats with valid token → not 401."""
        with patch("routers.schedule.get_focus_monitor") as mock_fm:
            fm = MagicMock()
            fm.get_pomodoro_stats.return_value = {"today": 0, "total": 0}
            mock_fm.return_value = fm
            resp = app_with_session.get(
                "/api/pomodoro/stats",
                headers={"Authorization": "Bearer sched-valid-token"},
            )
        assert resp.status_code != 401

    def test_pomodoro_stats_missing_token_returns_401(self, app_with_session):
        """GET /pomodoro/stats without token → 401."""
        resp = app_with_session.get("/api/pomodoro/stats")
        assert resp.status_code == 401

    def test_check_overlap_missing_params_returns_400_not_500(self, app_with_session):
        """check-overlap with valid token but missing start_iso/end_iso → 400, not 500.

        Regression guard (W6): _parse_iso(None) must raise 400, not an uncaught
        AttributeError that surfaces as 500 for authenticated callers.
        """
        resp = app_with_session.get(
            "/api/schedule/check-overlap",
            headers={"Authorization": "Bearer sched-valid-token"},
        )
        assert resp.status_code == 400


class TestScheduleUserIdDerivation:
    """REQ-4: user_id derived from session.get('role', 'guest') — default 'guest' preserved."""

    def test_list_events_uses_role_as_user_id(self, app_with_session):
        """Confirm user_id passed to service is session role (not hardcoded value)."""
        captured = {}
        with patch("routers.schedule.get_schedule_service") as mock_svc:
            svc = MagicMock()
            def capture_get_events(user_id, from_ts, to_ts):
                captured["user_id"] = user_id
                return []
            svc.get_events_in_range.side_effect = capture_get_events
            mock_svc.return_value = svc
            app_with_session.get(
                "/api/schedule/events",
                headers={"Authorization": "Bearer sched-valid-token"},
            )
        # session role is "user" for sched-valid-token
        assert captured.get("user_id") == "user"

    def test_list_events_defaults_to_guest_when_role_absent(self, app_with_guest_session):
        """When session has no role key, user_id defaults to 'guest' (REQ-4)."""
        captured = {}
        with patch("routers.schedule.get_schedule_service") as mock_svc:
            svc = MagicMock()
            def capture_get_events(user_id, from_ts, to_ts):
                captured["user_id"] = user_id
                return []
            svc.get_events_in_range.side_effect = capture_get_events
            mock_svc.return_value = svc
            app_with_guest_session.get(
                "/api/schedule/events",
                headers={"Authorization": "Bearer sched-guest-token"},
            )
        assert captured.get("user_id") == "guest"
