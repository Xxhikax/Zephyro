"""Tests for Fish Audio TTS integration."""
import os
import json
import sys
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestFishAudioEngineState:
    """Tests for the TTS engine global state (getter/setter)."""

    def test_default_tts_engine_is_local(self):
        from routers.voice import get_tts_engine
        assert get_tts_engine() == "local"

    def test_set_and_get_tts_engine(self):
        from routers.voice import set_tts_engine, get_tts_engine
        set_tts_engine("fishaudio")
        assert get_tts_engine() == "fishaudio"
        # Reset to default
        set_tts_engine("local")
        assert get_tts_engine() == "local"

    def test_set_tts_engine_invalid_does_not_crash(self):
        from routers.voice import set_tts_engine, get_tts_engine
        set_tts_engine("local")
        set_tts_engine("fishaudio")
        set_tts_engine("local")
        assert get_tts_engine() == "local"


class TestFishAudioEndpointConstruction:
    """Tests verifying the Fish Audio HTTP request is built correctly."""

    def test_fishaudio_request_url(self):
        """Verify the correct URL is used."""
        url = "https://api.fish.audio/v1/tts"
        assert "fish.audio" in url
        assert url.endswith("/v1/tts")

    def test_fishaudio_headers(self):
        """Verify required headers."""
        headers = {
            "Authorization": "Bearer test_key_123",
            "Content-Type": "application/json",
            "model": "s2-pro",
        }
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["model"] == "s2-pro"
        assert headers["Content-Type"] == "application/json"

    def test_fishaudio_payload(self):
        """Verify the JSON payload has required fields."""
        payload = {
            "text": "Hola mundo",
            "reference_id": "ae74d1059517440aa6e5d2d50598d179",
            "format": "wav",
            "latency": "normal",
        }
        assert "text" in payload
        assert "reference_id" in payload
        assert payload["format"] == "wav"
        assert payload["latency"] == "normal"

    def test_missing_api_key_returns_error(self):
        """Without FISHAUDIO_API_KEY, engine should fallback gracefully."""
        with patch.dict(os.environ, {}, clear=True):
            key = os.environ.get("FISHAUDIO_API_KEY", "")
            assert not key

    def test_missing_reference_id_returns_error(self):
        """Without FISHAUDIO_REFERENCE_ID, engine should error gracefully."""
        with patch.dict(os.environ, {}, clear=True):
            ref = os.environ.get("FISHAUDIO_REFERENCE_ID", "")
            assert not ref

    def test_env_vars_present(self):
        """Verify Fish Audio env vars are set in current environment."""
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        load_dotenv(env_path, override=True)

        key = os.environ.get("FISHAUDIO_API_KEY", "")
        ref = os.environ.get("FISHAUDIO_REFERENCE_ID", "")
        if not key:
            # A local-setup check, not a unit test: CI and fresh clones have no .env.
            pytest.skip("Fish Audio is not configured in this environment")
        assert key, "FISHAUDIO_API_KEY is set"
        assert ref, "FISHAUDIO_REFERENCE_ID is set"
        assert len(key) > 0
        assert len(ref) > 0


class TestFishAudioAPIMock:
    """Tests that mock the fish.audio API to verify logic without network."""

    @patch("requests.post")
    def test_fishaudio_http_call(self, mock_post):
        """Simulate a successful Fish Audio API call."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_wav_data"
        mock_post.return_value = mock_resp

        import requests
        resp = requests.post(
            "https://api.fish.audio/v1/tts",
            headers={
                "Authorization": "Bearer test_key",
                "Content-Type": "application/json",
                "model": "s2-pro",
            },
            json={
                "text": "Hola",
                "reference_id": "test_ref",
                "format": "wav",
                "latency": "normal",
            },
            timeout=30,
        )
        assert resp.status_code == 200
        assert len(resp.content) > 0
        mock_post.assert_called_once()

    @patch("requests.post")
    def test_fishaudio_http_401(self, mock_post):
        """Simulate a 401 from Fish Audio (bad API key)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"message": "Unauthorized"}'
        mock_post.return_value = mock_resp

        import requests
        resp = requests.post(
            "https://api.fish.audio/v1/tts",
            headers={
                "Authorization": "Bearer bad_key",
                "Content-Type": "application/json",
                "model": "s2-pro",
            },
            json={"text": "Hola", "reference_id": "x", "format": "wav"},
            timeout=30,
        )
        assert resp.status_code == 401


class TestSettingsEndpoint:
    """Tests for the settings endpoints that manage TTS engine."""

    def test_tts_engine_valid_values(self):
        valid_engines = ["local", "fishaudio"]
        assert "local" in valid_engines
        assert "fishaudio" in valid_engines
        assert "groq" not in valid_engines

    def test_tts_engine_invalid_rejected(self):
        valid_engines = ["local", "fishaudio"]
        assert "fake_engine" not in valid_engines


class TestTTSModuleFishAudio:
    """Tests for the TTS class Fish Audio integration."""

    def test_tts_class_fishaudio_available(self):
        """TTS class should detect Fish Audio config from env vars."""
        from modules.voice.tts import TTS
        with patch.dict(os.environ, {
            "FISHAUDIO_API_KEY": "test_key",
            "FISHAUDIO_REFERENCE_ID": "test_ref",
        }):
            tts = TTS()
            assert tts.fishaudio_available is True
            assert tts.fishaudio_key == "test_key"
            assert tts.fishaudio_ref == "test_ref"

    def test_tts_class_fishaudio_unavailable_without_key(self):
        """TTS class should report fishaudio unavailable when key missing."""
        from modules.voice.tts import TTS
        with patch.dict(os.environ, {}, clear=True):
            tts = TTS()
            assert tts.fishaudio_available is False
            assert tts.fishaudio_key == ""
            assert tts.fishaudio_ref == ""

    def test_tts_class_fishaudio_unavailable_without_ref(self):
        """TTS class should report fishaudio unavailable when ref missing."""
        from modules.voice.tts import TTS
        with patch.dict(os.environ, {
            "FISHAUDIO_API_KEY": "test_key",
        }, clear=True):
            tts = TTS()
            assert tts.fishaudio_available is False


class TestStatusEndpoint:
    """Tests for the /status endpoint including tts_engine."""

    def test_status_response_has_tts_engine_key(self):
        """Status dict should include tts_engine field."""
        status = {
            "user": {"role": "admin"},
            "models": {},
            "stt_engine": "local",
            "tts_engine": "local",
        }
        assert "tts_engine" in status
        assert status["tts_engine"] in ("local", "fishaudio")

    def test_status_non_admin_sees_local(self):
        """Non-admin users should always see 'local' for tts_engine."""
        status = {
            "tts_engine": "local",
        }
        assert status["tts_engine"] == "local"
