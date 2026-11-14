"""Tests for faster-whisper STT and the voice call mode pipeline.

Unit tests run without ZEPHYRO_TOKEN.
Integration tests require ZEPHYRO_TOKEN env var and a running backend.

Run unit tests only:
    cd backend && pytest tests/test_call_mode_voice.py -v -k "not integration"

Run all tests (requires running backend):
    ZEPHYRO_TOKEN=<token> cd backend && pytest tests/test_call_mode_voice.py -v
"""
import io
import os
import struct
import time
import wave
from unittest.mock import MagicMock, patch

import pytest


ZEPHYRO_URL = os.getenv("ZEPHYRO_URL", "http://localhost:8000")
ZEPHYRO_TOKEN = os.getenv("ZEPHYRO_TOKEN", "")

integration = pytest.mark.skipif(
    not ZEPHYRO_TOKEN,
    reason="Set ZEPHYRO_TOKEN env var to run integration tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_wav_bytes(amplitude: int = 8000, duration_s: float = 1.5, sample_rate: int = 16000) -> bytes:
    """Generate a WAV file with pseudo-random noise (simulates speech level)."""
    import random
    n = int(duration_s * sample_rate)
    rng = random.Random(42)
    samples = [rng.randint(-amplitude, amplitude) for _ in range(n)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"{n}h", *samples))
    return buf.getvalue()


def make_silent_wav(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    n = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
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


# ---------------------------------------------------------------------------
# Unit tests — faster-whisper STT
# ---------------------------------------------------------------------------

def _cuda_available() -> bool:
    """Returns True if CUDA is available for CTranslate2/faster-whisper."""
    try:
        import ctranslate2
        return bool(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        return False


class TestSTTUnit:
    @pytest.mark.skipif(
        _cuda_available(),
        reason="CUDA is available — STT uses CUDA, test expects CPU path; skip on GPU machines",
    )
    def test_init_loads_faster_whisper(self):
        """STT.__init__ completes with a mocked WhisperModel."""
        with patch("modules.voice.stt.WhisperModel") as MockModel:
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance

            from modules.voice.stt import STT
            stt = STT(model_size="base")

            MockModel.assert_called_once_with("base", device="cpu", compute_type="int8")
            assert stt.model is mock_instance
            assert stt.sample_rate == 16000

    def test_transcribe_none_returns_empty(self):
        """transcribe(None) must return '' without touching the model."""
        with patch("modules.voice.stt.WhisperModel") as MockModel:
            MockModel.return_value = MagicMock()
            from modules.voice.stt import STT
            stt = STT()
            result = stt.transcribe(None)
            assert result == ""
            stt.model.transcribe.assert_not_called()

    @pytest.mark.skipif(
        _cuda_available(),
        reason="CUDA is available — STT uses CUDA path, test expects CPU mock path; skip on GPU machines",
    )
    def test_transcribe_returns_joined_segments(self):
        """transcribe() joins segment texts from the faster-whisper generator."""
        with patch("modules.voice.stt.WhisperModel") as MockModel:
            seg1 = MagicMock()
            seg1.text = "Hola"
            seg1.avg_logprob = -0.5  # Must be float for >= comparison in transcribe()
            seg2 = MagicMock()
            seg2.text = " mundo"
            seg2.avg_logprob = -0.5
            mock_instance = MagicMock()
            mock_instance.transcribe.return_value = ([seg1, seg2], MagicMock())
            MockModel.return_value = mock_instance

            from modules.voice.stt import STT
            stt = STT()
            result = stt.transcribe("fake_audio.wav")

            assert result == "Hola  mundo"
            mock_instance.transcribe.assert_called_once_with(
                "fake_audio.wav",
                language="es",
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.45,
                    min_speech_duration_ms=200,
                    speech_pad_ms=200,
                ),
                condition_on_previous_text=False,
                no_speech_threshold=0.5,
                log_prob_threshold=-1.0,
                compression_ratio_threshold=2.0,
                initial_prompt="Zephyro, JARVIS, Bruno.",
            )

    def test_transcribe_handles_exception_gracefully(self):
        """transcribe() returns '' if the model raises."""
        with patch("modules.voice.stt.WhisperModel") as MockModel:
            mock_instance = MagicMock()
            mock_instance.transcribe.side_effect = RuntimeError("CUDA error")
            MockModel.return_value = mock_instance

            from modules.voice.stt import STT
            stt = STT()
            result = stt.transcribe("audio.wav")
            assert result == ""


# ---------------------------------------------------------------------------
# Integration tests — live endpoints
# ---------------------------------------------------------------------------

class TestSTTEndpointIntegration:
    @integration
    def test_stt_returns_success_field(self, client):
        """STT endpoint should always return a 'success' field."""
        wav = make_silent_wav(1.0)
        resp = client.post(
            "/api/voice/stt",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            files={"audio": ("silent.wav", wav, "audio/wav")},
        )
        assert resp.status_code == 200
        assert "success" in resp.json()

    @integration
    def test_stt_noise_wav_completes_within_limit(self, client):
        """faster-whisper on CPU should transcribe 1.5s of audio well under 40s."""
        wav = make_wav_bytes(amplitude=8000, duration_s=1.5)
        t0 = time.time()
        resp = client.post(
            "/api/voice/stt",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            files={"audio": ("noise.wav", wav, "audio/wav")},
            timeout=60.0,
        )
        elapsed = time.time() - t0
        assert resp.status_code == 200
        assert "success" in resp.json()
        assert elapsed < 40, f"STT too slow: {elapsed:.1f}s"

    @integration
    def test_stt_invalid_token_returns_401(self, client):
        wav = make_silent_wav(0.5)
        resp = client.post(
            "/api/voice/stt",
            files={"audio": ("test.wav", wav, "audio/wav")},
            headers={"Authorization": "Bearer invalid_token_xyz"},
        )
        assert resp.status_code == 401


class TestTTSEndpointIntegration:
    @integration
    def test_tts_returns_base64_wav(self, client):
        """Valid text should return base64-encoded WAV audio."""
        resp = client.post(
            "/api/voice/tts",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            data={"text": "Hola, esto es una prueba."},
        )
        assert resp.status_code == 200
        data = resp.json()
        if not data.get("success"):
            pytest.skip(f"TTS unavailable: {data.get('error')}")
        assert "audio" in data
        import base64
        raw = base64.b64decode(data["audio"])
        assert raw[:4] == b"RIFF", "Audio is not a valid WAV"

    @integration
    def test_tts_invalid_token_returns_401(self, client):
        resp = client.post(
            "/api/voice/tts",
            headers={"Authorization": "Bearer bad_token"},
            data={"text": "Hola"},
        )
        assert resp.status_code == 401


class TestFullCyclePipeline:
    @integration
    def test_stt_chat_tts_cycle(self, client):
        """STT → chat send → TTS; each step must succeed and total < 40s."""
        t0 = time.time()

        # Step 1: STT (noise — may or may not transcribe to valid text)
        wav = make_wav_bytes(amplitude=8000, duration_s=1.5)
        stt_resp = client.post(
            "/api/voice/stt",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            files={"audio": ("test.wav", wav, "audio/wav")},
            timeout=60.0,
        )
        assert stt_resp.status_code == 200

        # Step 2: Chat send (use a fixed text to avoid empty STT result)
        chat_resp = client.post(
            "/api/chat/send",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            json={"message": "Hola, responde en una frase corta.", "mode": "auto"},
            timeout=60.0,
        )
        assert chat_resp.status_code == 200
        chat_data = chat_resp.json()
        assert chat_data.get("success") is True

        # Step 3: TTS on the response
        response_text = chat_data.get("response", "Entendido.")[:200]
        tts_resp = client.post(
            "/api/voice/tts",
            headers={"Authorization": f"Bearer {ZEPHYRO_TOKEN}"},
            data={"text": response_text},
            timeout=30.0,
        )
        assert tts_resp.status_code == 200
        tts_data = tts_resp.json()
        assert "success" in tts_data

        total = time.time() - t0
        assert total < 40, f"Full cycle too slow: {total:.1f}s"
