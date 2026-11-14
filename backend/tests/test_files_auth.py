"""Smoke tests for routers/files.py — auth migration (Slice C).

Verifies:
  - /files/list: valid token -> 200, missing token -> 401
  - /files/upload: valid token -> 200, missing token -> 401

Fully mocked: web_app.sessions pre-populated; NO real disk I/O (UPLOAD_DIR patched).
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN_TOKEN = "admin-slice-c-files-token"
GUEST_TOKEN = "guest-slice-c-files-token"


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


@pytest.fixture
def client(tmp_path):
    import importlib
    import web_app

    web_app.sessions = _make_sessions()

    from fastapi import FastAPI
    import routers.files as files_mod
    importlib.reload(files_mod)

    # Patch UPLOAD_DIR so no real disk access happens
    with patch.object(files_mod, "UPLOAD_DIR", tmp_path):
        app = FastAPI()
        app.include_router(files_mod.router, prefix="/api")
        yield TestClient(app)


# ── /files/list ───────────────────────────────────────────────────────────────

class TestFilesListAuth:
    def test_list_valid_token_returns_200(self, client):
        resp = client.get("/api/files/list", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "files" in data

    def test_list_guest_token_returns_200(self, client):
        # /files/list is auth-only (not admin), so guest is allowed
        resp = client.get("/api/files/list", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 200

    def test_list_missing_token_returns_401(self, client):
        resp = client.get("/api/files/list")
        assert resp.status_code == 401

    def test_list_invalid_token_returns_401(self, client):
        resp = client.get("/api/files/list", headers={"Authorization": "Bearer bogus-token"})
        assert resp.status_code == 401


# ── /files/upload ─────────────────────────────────────────────────────────────

class TestFilesUploadAuth:
    def _upload(self, client, token_param: str | None, content: bytes = b"hello test"):
        """POST multipart to /files/upload. token via Authorization header."""
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post(
            "/api/files/upload",
            headers=headers,
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )

    def test_upload_valid_token_returns_200(self, client):
        resp = self._upload(client, ADMIN_TOKEN)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "file_info" in data

    def test_upload_guest_token_returns_200(self, client):
        # /files/upload is auth-only, so guest is allowed
        resp = self._upload(client, GUEST_TOKEN)
        assert resp.status_code == 200

    def test_upload_missing_token_returns_401(self, client):
        resp = self._upload(client, None)
        assert resp.status_code == 401

    def test_upload_invalid_token_returns_401(self, client):
        resp = self._upload(client, "bad-token-xyz")
        assert resp.status_code == 401
