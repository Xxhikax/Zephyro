"""Tests for the STT (Speech-to-Text) endpoint and hallucination filter."""
import io
import struct
import wave
import pytest
import numpy as np


# ---------------------------------------------------------------------------
# Unit tests for the hallucination filter (no server required)
# ---------------------------------------------------------------------------

def make_wav_bytes(samples: list[int], sample_rate: int = 16000) -> bytes:
    """Create a minimal WAV file in memory from 16-bit PCM samples."""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"{len(samples)}h", *samples))
    return buf.getvalue()


def make_silent_wav(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    n = int(duration_s * sample_rate)
    return make_wav_bytes([0] * n, sample_rate)


def make_noise_wav(duration_s: float = 1.0, sample_rate: int = 16000, amplitude: int = 8000) -> bytes:
    rng = np.random.default_rng(42)
    n = int(duration_s * sample_rate)
    samples = (rng.uniform(-amplitude, amplitude, n)).astype(np.int16).tolist()
    return make_wav_bytes(samples, sample_rate)


# Import the filter directly
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routers.voice import _is_whisper_hallucination


class TestHallucinationFilter:
    def test_empty_string_is_hallucination(self):
        assert _is_whisper_hallucination("") is True

    def test_very_short_text_is_hallucination(self):
        assert _is_whisper_hallucination("ab") is True

    def test_normal_text_not_hallucination(self):
        assert _is_whisper_hallucination("Hola Zephyro") is False

    def test_thanks_for_watching_is_hallucination(self):
        assert _is_whisper_hallucination("Thanks for watching this video!") is True

    def test_amara_is_hallucination(self):
        assert _is_whisper_hallucination("Subtítulos por amara.org") is True

    def test_suscribete_is_hallucination(self):
        assert _is_whisper_hallucination("No olvides suscribirte al canal") is True

    def test_case_insensitive(self):
        assert _is_whisper_hallucination("THANKS FOR WATCHING") is True

    def test_three_char_min_boundary(self):
        assert _is_whisper_hallucination("abc") is False

    def test_real_command(self):
        assert _is_whisper_hallucination("Abre el navegador") is False


# ---------------------------------------------------------------------------
# WAV RMS silence detection logic (mirrors the router's inline check)
# ---------------------------------------------------------------------------

def compute_normalized_rms(wav_bytes: bytes) -> float:
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
    samples = struct.unpack(f"{len(frames) // 2}h", frames)
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
    return rms / 32768


class TestSilenceDetection:
    def test_silent_wav_below_threshold(self):
        wav = make_silent_wav(1.0)
        rms = compute_normalized_rms(wav)
        assert rms < 0.005, f"Expected RMS < 0.005 for silence, got {rms}"

    def test_noise_wav_above_threshold(self):
        wav = make_noise_wav(1.0, amplitude=8000)
        rms = compute_normalized_rms(wav)
        assert rms > 0.005, f"Expected RMS > 0.005 for noise, got {rms}"

    def test_very_low_amplitude_below_threshold(self):
        wav = make_noise_wav(1.0, amplitude=50)
        rms = compute_normalized_rms(wav)
        assert rms < 0.005

    def test_high_amplitude_clearly_above(self):
        wav = make_noise_wav(1.0, amplitude=20000)
        rms = compute_normalized_rms(wav)
        assert rms > 0.005


# ---------------------------------------------------------------------------
# WAV file creation sanity tests
# ---------------------------------------------------------------------------

class TestWavCreation:
    def test_wav_has_correct_sample_rate(self):
        wav = make_silent_wav(0.5)
        buf = io.BytesIO(wav)
        with wave.open(buf, 'rb') as wf:
            assert wf.getframerate() == 16000

    def test_wav_is_mono(self):
        wav = make_silent_wav(0.5)
        buf = io.BytesIO(wav)
        with wave.open(buf, 'rb') as wf:
            assert wf.getnchannels() == 1

    def test_wav_duration_correct(self):
        wav = make_silent_wav(2.0)
        buf = io.BytesIO(wav)
        with wave.open(buf, 'rb') as wf:
            duration = wf.getnframes() / wf.getframerate()
        assert abs(duration - 2.0) < 0.01

    def test_empty_audio_handled(self):
        """Zero-length samples produce valid WAV but empty frames."""
        wav = make_wav_bytes([])
        buf = io.BytesIO(wav)
        with wave.open(buf, 'rb') as wf:
            assert wf.getnframes() == 0
