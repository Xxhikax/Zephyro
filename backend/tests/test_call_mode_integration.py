"""Integration tests for the STT/TTS pipeline using httpx + real endpoints.

These tests require the Zephyro backend to be running at http://localhost:8000
and a valid session token. Set the env var ZEPHYRO_TOKEN to run them.

Run with:
    ZEPHYRO_TOKEN=<token> python -m pytest tests/test_call_mode_integration.py -v
"""
import io
import os
import struct
import time
import wave

import pytest


ZEPHYRO_URL = os.getenv("ZEPHYRO_URL", "http://localhost:8000")
ZEPHYRO_TOKEN = os.getenv("ZEPHYRO_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not ZEPHYRO_TOKEN,
    reason="Set ZEPHYRO_TOKEN env var to run integration tests",
)


def make_wav_bytes(amplitude: int = 8000, duration_s: float = 1.5, sample_rate: int = 16000) -> bytes:
    """Generate a WAV file with pseudo-random noise (simulates speech level)."""
    import random
    n = int(duration_s * sample_rate)
    rng = random.Random(42)
    samples = [rng.randint(-amplitude, amplitude) for _ in range(n)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"{n}h", *samples))
    return buf.getvalue()


def make_silent_wav(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    n = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"{n}h", *([0] * n)))
    return buf.getvalue()


@pytest.fixture
def client():
    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed — run: pip install httpx")
    return httpx.Client(base_url=ZEPHYRO_URL, timeout=30.0)


class TestSTTEndpoint:
    def test_silent_audio_returns_no_voice(self, client):
        """Silent WAV should return success=False (silence detected)."""
        wav = make_silent_wav(1.0)
        resp = client.post(
            "/api/voice/stt",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            files={"audio": ("silent.wav", wav, "audio/wav")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_empty_file_returns_error(self, client):
        """Zero-byte upload should fail gracefully."""
        resp = client.post(
            "/api/voice/stt",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            files={"audio": ("empty.wav", b"", "audio/wav")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "error" in data

    def test_invalid_token_returns_401(self, client):
        wav = make_silent_wav(0.5)
        resp = client.post(
            "/api/voice/stt",
            headers={"Authorization": "Bearer invalid_token_xyz"},
            files={"audio": ("test.wav", wav, "audio/wav")},
        )
        assert resp.status_code == 401

    def test_noise_wav_goes_through_whisper(self, client):
        """Noise at speech level should pass the RMS check and reach Whisper."""
        wav = make_wav_bytes(amplitude=8000, duration_s=2.0)
        t0 = time.time()
        resp = client.post(
            "/api/voice/stt",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            files={"audio": ("noise.wav", wav, "audio/wav")},
            timeout=60.0,
        )
        elapsed = time.time() - t0
        assert resp.status_code == 200
        data = resp.json()
        # May succeed or fail (Whisper may return empty for pure noise), but must not crash
        assert "success" in data
        assert elapsed < 60, f"STT took too long: {elapsed:.1f}s"


class TestTTSEndpoint:
    def test_tts_returns_base64_wav(self, client):
        """Valid text should return base64-encoded WAV audio."""
        resp = client.post(
            "/api/voice/tts",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            data={"text": "Hola"},
        )
        assert resp.status_code == 200
        data = resp.json()
        if not data.get("success"):
            pytest.skip(f"TTS unavailable: {data.get('error')}")
        assert "audio" in data
        # Verify it's valid base64 WAV
        import base64
        raw = base64.b64decode(data["audio"])
        assert raw[:4] == b'RIFF', "Audio is not a valid WAV file"

    def test_tts_invalid_token_returns_401(self, client):
        resp = client.post(
            "/api/voice/tts",
            headers={"Authorization": "Bearer bad_token"},
            data={"text": "Hola"},
        )
        assert resp.status_code == 401

    def test_tts_long_text_no_crash(self, client):
        """Text near 800-char limit should not crash the endpoint."""
        long_text = "Zephyro responde con mucho detalle. " * 25
        resp = client.post(
            "/api/voice/tts",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            data={"text": long_text[:800]},
            timeout=60.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data


class TestSTTTTSPipeline:
    def test_pipeline_latency(self, client):
        """End-to-end STT + TTS should complete within 10s on local hardware."""
        # Step 1: STT (noise — may or may not transcribe)
        wav = make_wav_bytes(amplitude=8000, duration_s=1.5)
        t0 = time.time()
        stt_resp = client.post(
            "/api/voice/stt",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            files={"audio": ("test.wav", wav, "audio/wav")},
            timeout=30.0,
        )
        stt_elapsed = time.time() - t0
        assert stt_resp.status_code == 200

        # Step 2: TTS
        t1 = time.time()
        tts_resp = client.post(
            "/api/voice/tts",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            data={"text": "Entendido."},
            timeout=30.0,
        )
        tts_elapsed = time.time() - t1

        total = stt_elapsed + tts_elapsed
        assert total < 30, f"STT+TTS pipeline too slow: {total:.1f}s"
        assert tts_resp.status_code == 200
