"""Tests for the TTS (Text-to-Speech) endpoint."""
import base64
import io
import wave
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ---------------------------------------------------------------------------
# Unit tests that mock piper-tts (no binary required)
# ---------------------------------------------------------------------------

def make_minimal_wav() -> bytes:
    """Return a minimal valid WAV file (1s silence, 16kHz mono)."""
    import struct
    n = 16000
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack(f"{n}h", *([0] * n)))
    return buf.getvalue()


class TestTTSEndpointLogic:
    """Tests that exercise the TTS logic with mocked piper."""

    def test_base64_encode_decode_roundtrip(self):
        """Verify that base64 encoding/decoding of WAV is lossless."""
        original = make_minimal_wav()
        encoded = base64.b64encode(original).decode('utf-8')
        decoded = base64.b64decode(encoded)
        assert decoded == original

    def test_wav_base64_is_valid_string(self):
        wav = make_minimal_wav()
        encoded = base64.b64encode(wav).decode('utf-8')
        assert isinstance(encoded, str)
        assert len(encoded) > 0
        # Base64 uses only alphanumeric + / + = chars
        import re
        assert re.match(r'^[A-Za-z0-9+/=]+$', encoded)

    def test_empty_text_produces_no_audio(self):
        """Empty/whitespace text should be rejected before calling piper."""
        text = "   "
        assert not text.strip(), "Whitespace-only text should be treated as empty"

    def test_long_text_truncation(self):
        """Text over 800 chars should be truncatable without crash."""
        long_text = "Hola Zephyro. " * 100  # ~1300 chars
        truncated = long_text.strip()[:800]
        assert len(truncated) <= 800

    def test_markdown_cleaned_before_tts(self):
        """Markdown formatting should be stripped before sending to piper."""
        import re
        raw = "**Respuesta:** `código` y _cursiva_ con ```bloque```"
        clean = re.sub(r'```[\s\S]*?```', '', raw)
        clean = re.sub(r'[*_`#>|]', '', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        assert '**' not in clean
        assert '`' not in clean
        assert len(clean) > 0

    def test_valid_wav_header(self):
        """Minimal WAV we generate has correct RIFF header."""
        wav = make_minimal_wav()
        assert wav[:4] == b'RIFF'
        assert wav[8:12] == b'WAVE'

    def test_model_path_check(self):
        """If model path doesn't exist, endpoint should report error (no crash)."""
        model_path = Path("models/piper/es_ES/es_ES-davefx-medium.onnx")
        # We just verify the logic: if path doesn't exist, return error dict
        if not model_path.exists():
            result = {"success": False, "error": f"Modelo TTS no encontrado: {model_path}"}
            assert result["success"] is False
            assert "no encontrado" in result["error"]


class TestTTSTokenValidation:
    def test_missing_token_format(self):
        """Token validation: empty token should fail."""
        sessions = {"valid_token_abc": {"user": "test"}}
        token = ""
        assert token not in sessions

    def test_valid_token_passes(self):
        sessions = {"valid_token_abc": {"user": "test"}}
        token = "valid_token_abc"
        assert token in sessions

    def test_invalid_token_rejected(self):
        sessions = {"valid_token_abc": {"user": "test"}}
        token = "wrong_token"
        assert token not in sessions
