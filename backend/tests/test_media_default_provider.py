"""
Tests del provider por defecto para intents de media sin plataforma.

Contrato anti-alucinación: un intent de media detectado por
_extract_media_entities SIEMPRE lleva un provider real (default
youtube_music) y nunca cae al LLM plano, que alucinaba
"Reproduciendo..." sin ejecutar nada (bug de la sesión call-mode
2026-06-12: "Produce el álbum El Camino de The Black Keys").
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.router import _extract_media_entities, _MEDIA_DEFAULT_PROVIDER  # noqa: E402


def test_unknown_provider_defaults_to_youtube_music():
    result = _extract_media_entities("produce el álbum El Camino de The Black Keys")
    assert result is not None
    assert result["provider"] == _MEDIA_DEFAULT_PROVIDER == "youtube_music"
    assert result["media_type"] == "album"
    assert result["needs_clarification"] is False
    assert result["url"] is not None and "music.youtube.com" in result["url"]


def test_explicit_provider_unchanged():
    result = _extract_media_entities("reproduce lonely boy en spotify")
    assert result is not None
    assert result["provider"] == "spotify"
    assert result["needs_clarification"] is False


@pytest.mark.parametrize("text", [
    "produce el álbum harlequin",
    "reproduce gold on the ceiling",
    "pon música de los redondos",
    "toca el álbum el camino",
    "reproducí little black submarines",
])
def test_media_never_needs_clarification(text):
    result = _extract_media_entities(text)
    assert result is not None, f"Debe detectarse media intent en: {text!r}"
    assert result["needs_clarification"] is False
    assert result["provider"] != "unknown"


def test_non_media_text_still_returns_none():
    # Sin keyword de acción de media → None (no se infla la detección)
    assert _extract_media_entities("qué hora es en tokio") is None
