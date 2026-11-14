"""Smoke tests for routers/voice.py — auth migration (Slice D backend).

Verifies that every voice HTTP endpoint enforces require_auth:
  - valid token via ?token= URL param -> 200
  - missing token -> 401

Fully mocked: web_app.sessions pre-populated; NO real STT/TTS model, audio
processing, or filesystem operations beyond tmp_path.

NOTE: synthesize_tts and STT are mocked at the module level to avoid loading
Whisper or Piper during the test run.
"""
from __future__ import annotations

import importlib
import io
import struct
import sys
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN_TOKEN = "admin-slice-d-voice-token"
GUEST_TOKEN = "guest-slice-d-voice-token"


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


def _make_silent_wav(duration_s: float = 0.2, sample_rate: int = 16000) -> bytes:
    """Generate a minimal silent WAV for upload tests."""
    n = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"{n}h", *([0] * n)))
    return buf.getvalue()


def _mock_orchestrator() -> MagicMock:
    orc = MagicMock()
    mock_stt = MagicMock()
    mock_stt.transcribe.return_value = ""
    orc._get_stt.return_value = mock_stt
    return orc


@pytest.fixture
def client(tmp_path):
    import web_app

    web_app.sessions = _make_sessions()

    import routers.voice as voice_mod
    importlib.reload(voice_mod)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(voice_mod.router, prefix="/api")
    app.state.orchestrator = _mock_orchestrator()

    # Patch synthesize_tts so /voice/tts never calls Piper/Fish Audio
    tts_mock = AsyncMock(return_value={"success": True, "audio": "bW9ja2VkYXVkaW8="})

    with patch.object(voice_mod, "UPLOAD_DIR", tmp_path), \
         patch.object(voice_mod, "synthesize_tts", tts_mock):
        yield TestClient(app)


# ── /voice/stt ────────────────────────────────────────────────────────────────

class TestVoiceSTTAuth:
    _WAV = _make_silent_wav()

    def _post(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post(
            "/api/voice/stt",
            headers=headers,
            files={"audio": ("test.wav", self._WAV, "audio/wav")},
        )

    def test_valid_admin_token_returns_200(self, client):
        resp = self._post(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_valid_guest_token_returns_200(self, client):
        resp = self._post(client, GUEST_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._post(client)
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client):
        resp = self._post(client, "bogus-voice-token")
        assert resp.status_code == 401


# ── /voice/tts ────────────────────────────────────────────────────────────────

class TestVoiceTTSAuth:
    def _post(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post("/api/voice/tts", headers=headers, data={"text": "Hola mundo"})

    def test_valid_admin_token_returns_200(self, client):
        resp = self._post(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_valid_guest_token_returns_200(self, client):
        resp = self._post(client, GUEST_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._post(client)
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client):
        resp = self._post(client, "bogus-tts-token")
        assert resp.status_code == 401


# ── /voice/diag ───────────────────────────────────────────────────────────────

class TestVoiceDiagAuth:
    _WAV = _make_silent_wav()

    def _post(self, client, token_param=None):
        headers = {"Authorization": f"Bearer {token_param}"} if token_param else {}
        return client.post(
            "/api/voice/diag",
            headers=headers,
            files={"audio": ("diag.wav", self._WAV, "audio/wav")},
        )

    def test_valid_admin_token_returns_200(self, client):
        resp = self._post(client, ADMIN_TOKEN)
        assert resp.status_code == 200

    def test_valid_guest_token_returns_200(self, client):
        resp = self._post(client, GUEST_TOKEN)
        assert resp.status_code == 200

    def test_missing_token_returns_401(self, client):
        resp = self._post(client)
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client):
        resp = self._post(client, "bogus-diag-token")
        assert resp.status_code == 401
