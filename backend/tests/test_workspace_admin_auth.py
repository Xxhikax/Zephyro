"""Smoke tests for routers/workspace.py admin endpoints — auth migration (Slice C).

Migrated admin endpoints tested:
  /workspace/info    (GET)
  /workspace/read    (POST)
  /workspace/create  (POST)
  /git/status        (POST)
  /git/info          (GET)
  /summaries/clear   (DELETE)
  /workspaces/{name} (DELETE)

Each: valid admin -> 2xx, missing token -> 401, non-admin valid token -> 403.

Auth-optional endpoints (profile/*, summaries, workspaces CRUD) are NOT tested here —
they are out of scope for Slice C.

Fully mocked: web_app.sessions, orchestrator services. NO real disk/git side effects.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN_TOKEN = "admin-slice-c-ws-token"
GUEST_TOKEN = "guest-slice-c-ws-token"


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


def _mock_orchestrator():
    orc = MagicMock()
    # file_ops
    orc.file_ops.workspace_info.return_value = {"path": "/tmp", "files": []}
    orc.file_ops.read.return_value = (True, "file content")
    orc.file_ops.create_file.return_value = (True, "created")
    # git_ops
    orc.git_ops.repo_path = "/tmp"
    orc.git_ops.status = AsyncMock(return_value=(True, {"branch": "main"}))
    orc.git_ops.get_repo_info = AsyncMock(return_value={"branch": "main", "remote": "origin"})
    # summarizer
    orc.summarizer.clear_summaries.return_value = None
    # workspace (session workspaces)
    orc.workspace.delete_workspace.return_value = True
    return orc


@pytest.fixture
def client():
    import importlib
    import web_app

    web_app.sessions = _make_sessions()

    from fastapi import FastAPI
    import routers.workspace as ws_mod
    importlib.reload(ws_mod)

    app = FastAPI()
    app.state.orchestrator = _mock_orchestrator()
    app.include_router(ws_mod.router, prefix="/api")
    return TestClient(app)


# ── Helper ────────────────────────────────────────────────────────────────────

def _auth(token):
    """Build Authorization header dict."""
    return {"Authorization": f"Bearer {token}"} if token else {}


# ── /workspace/info ───────────────────────────────────────────────────────────

class TestWorkspaceInfoAuth:
    def test_valid_admin_returns_200(self, client):
        resp = client.get("/api/workspace/info", headers=_auth(ADMIN_TOKEN))
        assert resp.status_code == 200
        assert "info" in resp.json()

    def test_missing_token_returns_401(self, client):
        resp = client.get("/api/workspace/info")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, client):
        resp = client.get("/api/workspace/info", headers=_auth(GUEST_TOKEN))
        assert resp.status_code == 403


# ── /workspace/read ───────────────────────────────────────────────────────────

class TestWorkspaceReadAuth:
    def test_valid_admin_returns_200(self, client):
        resp = client.post(
            "/api/workspace/read",
            json={"path": "/tmp/file.txt"},
            headers=_auth(ADMIN_TOKEN),
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_missing_token_returns_401(self, client):
        resp = client.post("/api/workspace/read", json={"path": "/tmp/file.txt"})
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, client):
        resp = client.post(
            "/api/workspace/read",
            json={"path": "/tmp/file.txt"},
            headers=_auth(GUEST_TOKEN),
        )
        assert resp.status_code == 403


# ── /workspace/create ─────────────────────────────────────────────────────────

class TestWorkspaceCreateAuth:
    def test_valid_admin_returns_200(self, client):
        resp = client.post(
            "/api/workspace/create",
            json={"path": "/tmp/newfile.txt", "content": "hello"},
            headers=_auth(ADMIN_TOKEN),
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_missing_token_returns_401(self, client):
        resp = client.post(
            "/api/workspace/create",
            json={"path": "/tmp/newfile.txt", "content": "hello"},
        )
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, client):
        resp = client.post(
            "/api/workspace/create",
            json={"path": "/tmp/newfile.txt", "content": "hello"},
            headers=_auth(GUEST_TOKEN),
        )
        assert resp.status_code == 403


# ── /git/status ───────────────────────────────────────────────────────────────

class TestGitStatusAuth:
    def test_valid_admin_returns_200(self, client):
        resp = client.post(
            "/api/git/status",
            json={"path": "/tmp"},
            headers=_auth(ADMIN_TOKEN),
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_missing_token_returns_401(self, client):
        resp = client.post("/api/git/status", json={"path": "/tmp"})
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, client):
        resp = client.post(
            "/api/git/status",
            json={"path": "/tmp"},
            headers=_auth(GUEST_TOKEN),
        )
        assert resp.status_code == 403


# ── /git/info ─────────────────────────────────────────────────────────────────

class TestGitInfoAuth:
    def test_valid_admin_returns_200(self, client):
        resp = client.get("/api/git/info", headers=_auth(ADMIN_TOKEN))
        assert resp.status_code == 200
        assert "branch" in resp.json()

    def test_missing_token_returns_401(self, client):
        resp = client.get("/api/git/info")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, client):
        resp = client.get("/api/git/info", headers=_auth(GUEST_TOKEN))
        assert resp.status_code == 403


# ── /summaries/clear ─────────────────────────────────────────────────────────

class TestSummariesClearAuth:
    def test_valid_admin_returns_200(self, client):
        resp = client.delete("/api/summaries/clear", headers=_auth(ADMIN_TOKEN))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_missing_token_returns_401(self, client):
        resp = client.delete("/api/summaries/clear")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, client):
        resp = client.delete("/api/summaries/clear", headers=_auth(GUEST_TOKEN))
        assert resp.status_code == 403


# ── /workspaces/{name} DELETE ─────────────────────────────────────────────────

class TestWorkspaceDeleteAuth:
    def test_valid_admin_returns_200(self, client):
        resp = client.delete("/api/workspaces/my-workspace", headers=_auth(ADMIN_TOKEN))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_missing_token_returns_401(self, client):
        resp = client.delete("/api/workspaces/my-workspace")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, client):
        resp = client.delete("/api/workspaces/my-workspace", headers=_auth(GUEST_TOKEN))
        assert resp.status_code == 403
