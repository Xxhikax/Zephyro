"""Smoke tests for routers/vision.py — auth migration (Slice D backend).

Verifies that every vision endpoint enforces require_auth:
  - valid token via ?token= URL param -> 200
  - missing token -> 401

Fully mocked: web_app.sessions pre-populated; NO real vision model, screen
capture, or disk I/O beyond a tmp_path for the upload directory.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN_TOKEN = "admin-slice-d-vision-token"
GUEST_TOKEN = "guest-slice-d-vision-token"


def _make_sessions():
    return {
        ADMIN_TOKEN: {
            "role": "admin",
            "name": "Bruno",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        },
        GUEST_TOKEN: {
            "role": "guest",
            "name": "Invitado",
            "authenticated": True,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        },
    }


def _mock_orchestrator(tmp_path: Path) -> MagicMock:
    orc = MagicMock()
    # vision_analyze.analyze returns a plain string (success path)
    orc.vision_analyze.analyze.return_value = "Mocked vision analysis"
    # vision_capture.capture returns a path to a fake screenshot
    fake_shot = tmp_path / "fake_screenshot.png"
    fake_shot.write_bytes(b"PNG\x00fake")
    orc.vision_capture.capture.return_value = str(fake_shot)
    return orc


@pytest.fixture
def client(tmp_path):
    import web_app

    web_app.sessions = _make_sessions()

    import routers.vision as vision_mod
    importlib.reload(vision_mod)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(vision_mod.router, prefix="/api")
    app.state.orchestrator = _mock_orchestrator(tmp_path)

    with patch.object(vision_mod, "UPLOAD_DIR", tmp_path):
        yield TestClient(app)


# ── /vision/analyze ───────────────────────────────────────────────────────────

class TestVisionAnalyzeAuth:
    def _post(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post(
            "/api/vision/analyze",
            headers=headers,
            files={"file": ("test.png", b"PNG\x00fake_image_data", "image/png")},
            data={"prompt": "Describe", "use_cloud": "false"},
        )

    def test_valid_admin_token_returns_200(self, client):
        resp = self._post(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_valid_guest_token_returns_200(self, client):
        # guests get use_cloud forced to False but still 200
        resp = self._post(client, GUEST_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._post(client)
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client):
        resp = self._post(client, "bogus-vision-token")
        assert resp.status_code == 401


# ── /vision/screen ────────────────────────────────────────────────────────────

class TestVisionScreenAuth:
    def _post(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post("/api/vision/screen", headers=headers, data={"prompt": "Describe la pantalla"})

    def test_valid_token_returns_200(self, client):
        resp = self._post(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._post(client)
        assert resp.status_code == 401


# ── /vision/screen-query ──────────────────────────────────────────────────────

class TestVisionScreenQueryAuth:
    def _post(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post("/api/vision/screen-query", headers=headers, data={"question": "Que ves?"})

    def test_valid_token_returns_200(self, client):
        resp = self._post(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._post(client)
        assert resp.status_code == 401


# ── /vision/continuous/start ──────────────────────────────────────────────────

class TestVisionContinuousStartAuth:
    def _post(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post("/api/vision/continuous/start", headers=headers, data={"interval": "5"})

    def test_valid_token_returns_200(self, client):
        with patch("asyncio.create_task", return_value=MagicMock()):
            resp = self._post(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._post(client)
        assert resp.status_code == 401


# ── /vision/continuous/stop ───────────────────────────────────────────────────

class TestVisionContinuousStopAuth:
    def _post(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post("/api/vision/continuous/stop", headers=headers)

    def test_valid_token_returns_200(self, client):
        resp = self._post(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._post(client)
        assert resp.status_code == 401


# ── /vision/continuous/status ─────────────────────────────────────────────────

class TestVisionContinuousStatusAuth:
    def _get(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.get("/api/vision/continuous/status", headers=headers)

    def test_valid_token_returns_200(self, client):
        resp = self._get(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._get(client)
        assert resp.status_code == 401
