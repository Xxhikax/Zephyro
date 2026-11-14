"""Smoke tests for routers/system.py — auth migration (Slice B).

Verifies:
  - 9 admin endpoints: require_admin enforced (valid admin→2xx, missing→401, non-admin→403)
  - 2 auth-only endpoints (/status, /system/needs-restart): require_auth (valid→2xx, missing→401)
  - /apps/open + /apps/focus + /apps/config: BORRADOS (2026-10-01) — ver
    TestAppsEndpointsAreGone
  - Public endpoints (/health, /config/stop_phrases, /config/exit_phrases)
    return 2xx with NO token

Fully mocked: web_app.sessions pre-populated; NO real process launch, browser, audio, or CDP.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN_TOKEN = "admin-slice-b-test-token"
GUEST_TOKEN = "guest-slice-b-test-token"


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


def _mock_orchestrator() -> MagicMock:
    orc = MagicMock()
    llm_status = {
        "local_available": True,
        "cloud_available": True,
        "cloud_provider": "gemini",
        "is_online": True,
    }
    orc.llm.get_status.return_value = llm_status
    orc.llm.anthropic_model = "claude-sonnet-5-5"
    orc.llm.deepseek_model = "deepseek-flash"
    orc.llm.set_cloud_provider.return_value = {"cloud_available": True, "cloud_provider": "gemini"}
    orc.llm.set_anthropic_model.return_value = {"success": True}
    orc.llm.set_deepseek_model.return_value = {"success": True}
    return orc


def _mock_living_state() -> MagicMock:
    ls = MagicMock()
    ls.get_error_log.return_value = []
    ls._telegram_enabled.return_value = False
    return ls


@pytest.fixture
def client():
    import importlib
    import web_app

    web_app.sessions = _make_sessions()

    from fastapi import FastAPI
    import routers.system as system_mod
    importlib.reload(system_mod)

    app = FastAPI()
    orc = _mock_orchestrator()
    app.state.orchestrator = orc
    app.state.living_state = _mock_living_state()
    app.include_router(system_mod.router, prefix="/api")
    return TestClient(app)


# ── Public endpoints — must work without any token ────────────────────────────

class TestPublicEndpoints:
    def test_health_no_token(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_stop_phrases_no_token(self, client):
        resp = client.get("/api/config/stop_phrases")
        assert resp.status_code == 200

    def test_exit_phrases_no_token(self, client):
        resp = client.get("/api/config/exit_phrases")
        assert resp.status_code == 200


# ── /status — require_auth ────────────────────────────────────────────────────

class TestStatusAuthRequired:
    def test_status_valid_admin(self, client):
        with patch("routers.voice.get_stt_engine", return_value="local"), \
             patch("routers.voice.get_tts_engine", return_value="local"):
            resp = client.get("/api/status", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "admin"
        assert resp.json()["models"]["anthropic_model"] == "claude-sonnet-5-5"
        assert resp.json()["models"]["deepseek_model"] == "deepseek-flash"

    def test_status_valid_guest(self, client):
        with patch("routers.voice.get_stt_engine", return_value="local"), \
             patch("routers.voice.get_tts_engine", return_value="local"):
            resp = client.get("/api/status", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "guest"
        assert resp.json()["models"]["deepseek_model"] is None

    def test_status_missing_token(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 401


# ── /system/needs-restart — require_auth ─────────────────────────────────────

class TestNeedsRestartAuthRequired:
    def test_needs_restart_valid_token(self, client):
        resp = client.get("/api/system/needs-restart", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert "needs_restart" in resp.json()

    def test_needs_restart_missing_token(self, client):
        resp = client.get("/api/system/needs-restart")
        assert resp.status_code == 401

    def test_needs_restart_guest_allowed(self, client):
        # require_auth only — guest is a valid session
        resp = client.get("/api/system/needs-restart", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 200


# ── Admin endpoints — require_admin ──────────────────────────────────────────

class TestAdminEndpoints:
    """9 admin endpoints: valid admin→2xx, missing token→401, non-admin→403."""

    # /system/error-log
    def test_error_log_admin(self, client):
        resp = client.get("/api/system/error-log", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200

    def test_error_log_missing(self, client):
        resp = client.get("/api/system/error-log")
        assert resp.status_code == 401

    def test_error_log_non_admin(self, client):
        resp = client.get("/api/system/error-log", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # /settings/provider
    def test_provider_admin(self, client):
        resp = client.post("/api/settings/provider?provider=gemini", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200

    def test_provider_missing(self, client):
        resp = client.post("/api/settings/provider?provider=gemini")
        assert resp.status_code == 401

    def test_provider_non_admin(self, client):
        resp = client.post("/api/settings/provider?provider=gemini", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # /settings/stt-engine
    def test_stt_engine_admin(self, client):
        with patch("routers.voice.set_stt_engine"):
            resp = client.post("/api/settings/stt-engine?engine=local", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200

    def test_stt_engine_missing(self, client):
        resp = client.post("/api/settings/stt-engine?engine=local")
        assert resp.status_code == 401

    def test_stt_engine_non_admin(self, client):
        resp = client.post("/api/settings/stt-engine?engine=local", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # /settings/tts-engine
    def test_tts_engine_admin(self, client):
        with patch("routers.voice.set_tts_engine"):
            resp = client.post("/api/settings/tts-engine?engine=local", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200

    def test_tts_engine_missing(self, client):
        resp = client.post("/api/settings/tts-engine?engine=local")
        assert resp.status_code == 401

    def test_tts_engine_non_admin(self, client):
        resp = client.post("/api/settings/tts-engine?engine=local", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # /settings/anthropic-model
    def test_anthropic_model_admin(self, client):
        resp = client.post("/api/settings/anthropic-model?model=claude-sonnet-4-6", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200

    def test_anthropic_model_missing(self, client):
        resp = client.post("/api/settings/anthropic-model?model=claude-sonnet-4-6")
        assert resp.status_code == 401

    def test_anthropic_model_non_admin(self, client):
        resp = client.post("/api/settings/anthropic-model?model=claude-sonnet-4-6", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    def test_anthropic_model_legacy_is_remapped(self, client):
        resp = client.post("/api/settings/anthropic-model?model=claude-sonnet-5", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["model"] == "claude-sonnet-5-5"

    # /settings/deepseek-model
    def test_deepseek_model_admin(self, client):
        resp = client.post("/api/settings/deepseek-model?model=deepseek-v4-pro", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["model"] == "deepseek-v4-pro"
        orc = client.app.state.orchestrator
        orc.llm.set_deepseek_model.assert_called_once_with("deepseek-v4-pro")

    def test_deepseek_model_legacy_is_remapped(self, client):
        resp = client.post("/api/settings/deepseek-model?model=deepseek-v4-flash", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["model"] == "deepseek-flash"

    def test_deepseek_model_invalid(self, client):
        resp = client.post("/api/settings/deepseek-model?model=gpt-4o", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 400
        client.app.state.orchestrator.llm.set_deepseek_model.assert_not_called()

    def test_deepseek_model_missing(self, client):
        resp = client.post("/api/settings/deepseek-model?model=deepseek-flash")
        assert resp.status_code == 401

    def test_deepseek_model_non_admin(self, client):
        resp = client.post("/api/settings/deepseek-model?model=deepseek-flash", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # /system/config (GET)
    def test_system_config_admin(self, client):
        resp = client.get("/api/system/config", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200

    def test_system_config_missing(self, client):
        resp = client.get("/api/system/config")
        assert resp.status_code == 401

    def test_system_config_non_admin(self, client):
        resp = client.get("/api/system/config", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # /system/config/toggle
    def test_config_toggle_admin(self, client):
        resp = client.post(
            "/api/system/config/toggle",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"key": "AUTO_CLOUD", "value": "true"},
        )
        assert resp.status_code == 200

    def test_config_toggle_missing(self, client):
        resp = client.post(
            "/api/system/config/toggle",
            json={"key": "AUTO_CLOUD", "value": "true"},
        )
        assert resp.status_code == 401

    def test_config_toggle_non_admin(self, client):
        resp = client.post(
            "/api/system/config/toggle",
            headers={"Authorization": f"Bearer {GUEST_TOKEN}"},
            json={"key": "AUTO_CLOUD", "value": "true"},
        )
        assert resp.status_code == 403

    # /system/reload-config
    def test_reload_config_admin(self, client):
        resp = client.post("/api/system/reload-config", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_reload_config_missing(self, client):
        resp = client.post("/api/system/reload-config")
        assert resp.status_code == 401

    def test_reload_config_non_admin(self, client):
        resp = client.post("/api/system/reload-config", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403

    # /system/restart (admin only — we only test auth, NOT the actual restart)
    def test_restart_missing(self, client):
        resp = client.post("/api/system/restart")
        assert resp.status_code == 401

    def test_restart_non_admin(self, client):
        resp = client.post("/api/system/restart", headers={"Authorization": f"Bearer {GUEST_TOKEN}"})
        assert resp.status_code == 403


# ── /apps/* — endpoints BORRADOS el 2026-10-01 ──────────────────────────────

class TestAppsEndpointsAreGone:
    """`/apps/open`, `/apps/focus` y `/apps/config` ya no existen.

    Ejecutaban acciones a nivel SO (`open_app` con un string arbitrario,
    `focus_window`) con `require_auth` como único control y SIN pasar por
    `core/action_risk_rules.classify_action`, que vive en el orchestrator —
    o sea que rodeaban el guardrail por completo. `/apps/focus` era además el
    camino que dejó la PC sin responder a los clicks (commit 891a44a).

    No tenían un solo llamador de producción: quedaron huérfanos cuando el
    ownership de apps se movió al backend y se borró el routing de
    `src/chat.js`. Estos tests están al revés a propósito — si alguien los
    reintroduce sin guardrail, acá se enteran.
    """

    def test_apps_open_is_gone(self, client):
        resp = client.post(
            "/api/apps/open",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"app": "spotify"},
        )
        assert resp.status_code == 404, (
            "/apps/open ejecuta acciones de SO sin pasar por classify_action"
        )

    def test_apps_focus_is_gone(self, client):
        resp = client.post(
            "/api/apps/focus",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"app": "spotify"},
        )
        assert resp.status_code == 404, (
            "/apps/focus es el camino de focus_window que congeló el escritorio"
        )

    def test_apps_config_is_gone(self, client):
        # Además no tenía auth: exponía las whitelists a cualquiera.
        assert client.get("/api/apps/config").status_code == 404
