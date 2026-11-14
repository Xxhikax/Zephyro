"""
Tests de los patrones de búsqueda proactiva para lanzamientos recientes.

Bug origen (sesión call-mode 2026-06-12): "hace pocos días salió el último
modelo de Anthropic..." no disparó búsqueda web; Zephyro respondió "no tengo
información, ¿quieres que investigue?" y hubo que pedirla explícitamente.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.integrations.api_registry import _ALWAYS_SEARCH_PATTERNS  # noqa: E402


def _matches_any(text: str) -> bool:
    q = text.lower()
    return any(re.search(p, q) for p in _ALWAYS_SEARCH_PATTERNS)


@pytest.mark.parametrize("text", [
    # La frase real de la sesión que motivó el fix
    "hace pocos días se salió el último modelo de anthropic que se llama fable 5",
    "salió hace pocos días el nuevo modelo de claude",
    "hace poco lanzaron una actualización de windows",
    "recientemente anunciaron la versión 2 del framework",
    "el nuevo modelo de openai es multimodal",
    "cuál es la última versión de edge",
    "acaba de salir el parche del juego",
    "qué trae el modelo más reciente de gemini",
    "anthropic anunció un modelo nuevo esta semana",
])
def test_recent_release_phrases_trigger_search(text):
    assert _matches_any(text), f"Debería disparar búsqueda: {text!r}"


@pytest.mark.parametrize("text", [
    "qué es python",
    "explícame qué es una función lambda",
    "cómo funciona un diccionario en python",
    "abre spotify",
])
def test_timeless_phrases_do_not_trigger_search(text):
    assert not _matches_any(text), f"NO debería disparar búsqueda: {text!r}"


def test_router_temporal_skip_includes_recent_release_phrases():
    """Las frases de lanzamiento reciente no deben fast-pathear como concepto."""
    import inspect
    from core import router

    source = inspect.getsource(router)
    for phrase in ("hace pocos días", "nuevo modelo de", "acaba de salir"):
        assert phrase in source, f"_temporal_skip debe incluir: {phrase!r}"
