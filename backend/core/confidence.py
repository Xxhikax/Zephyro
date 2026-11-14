"""
confidence — Señal de confianza unificada para el routing de Zephyro.

El router no mide confianza explícitamente: cae por capas hasta que algo
matchea. Pero cada decisión deja una huella en el campo ``_source`` del
análisis (``capa0_rules``, ``capa1_compound``, ``capa2_haiku``...). Este módulo
deriva un score 0..1 a partir de ESE origen, sin necesidad de un clasificador
nuevo. La premisa: cuanto más temprana y heurística la capa que resolvió el
intent, más confiable es; los fallbacks LLM y los caminos degradados valen menos.

`confidence_of()` es la única fuente de verdad. El ClarificationGate y el
orquestador multi-tarea consumen este score para decidir cuándo preguntar.
"""
from __future__ import annotations

from typing import Any, Mapping

# ── Mapa origen → confianza ──────────────────────────────────────────────
# Las claves son los valores de analysis["_source"] que emite router.py
# (y browser_intent_schema.py para el extractor LLM). Mantener sincronizado
# si se agregan nuevos _source en el router.
SOURCE_CONFIDENCE: dict[str, float] = {
    # CAPA 0 — reglas puras (saludos, math, playback): máxima certeza
    "capa0_rules": 1.0,
    "capa0_6_playback_ctl": 0.95,
    # CAPA 1 — heurísticas de keywords/regex: alta certeza
    "capa1_heuristic": 0.9,
    "capa1_compound": 0.9,
    "capa1_14c_shopping": 0.9,
    "capa1_browse_extract": 0.9,
    "capa1_media_entities": 0.9,
    "capa1_nav_subpath": 0.85,
    # CAPA 1.5 — extractor LLM estructurado (browser_intent_schema): media-alta
    "capa1.5_llm_extractor": 0.7,
    # Classifier registry-aware en cloud mode (cloud_intent_classifier, Fase 2):
    # mismo orden de certeza que el extractor LLM — entiende semánticamente pero
    # no es una regla dura. El gate puede clarificar si hay duda.
    "capa_cloud_classifier": 0.7,
    # CAPA 2 — Haiku clasificador de una palabra: media
    "capa2_haiku": 0.6,
    # CAPA 2 salteada porque su resultado no podía cambiar el ruteo (falló el guard
    # de tabla o el de verbo de acción → information/respond pase lo que pase).
    # MISMA confianza que capa2_haiku a propósito: el análisis resultante es idéntico
    # al que devolvía la rama else, así que el ClarificationGate debe decidir igual.
    "capa2_skipped_no_effect": 0.6,
    # Caminos degradados / fallback
    "capa1_sequence_skip_llm": 0.55,  # extracción manual del último target en cloud mode
    "cloud_fast_lane": 0.4,           # cloud mode sin router completo
    "fallback_information": 0.3,      # nada matcheó → se trató como info/chat
}

# Confianza por intent cuando no hay _source reconocible.
_INTENT_FALLBACK: dict[str, float] = {
    "automation": 0.5,
    "search": 0.5,
    "web_search": 0.5,
    "vision": 0.6,
    "screen_query": 0.6,
    "code_analysis": 0.6,
    "diagnostic": 0.6,
    "information": 0.5,
    "chat": 0.4,
}

# Default absoluto si no hay ni _source ni intent reconocible.
_DEFAULT_CONFIDENCE = 0.5


def confidence_of(analysis: Mapping[str, Any]) -> float:
    """
    Devuelve la confianza 0..1 de un análisis de routing.

    Cascada: un ``confidence`` explícito (lo pone CAPA 2 / el extractor LLM)
    gana; si no, se mapea ``_source``; si tampoco, se cae al fallback por
    ``intent``; en última instancia, _DEFAULT_CONFIDENCE.
    """
    if not isinstance(analysis, Mapping):
        return _DEFAULT_CONFIDENCE

    # 1. confidence explícito (CAPA 2 pone 0.75/0.9; el extractor pone _confidence)
    explicit = analysis.get("confidence")
    if explicit is None:
        explicit = analysis.get("_confidence")
    if isinstance(explicit, (int, float)):
        return _clamp(float(explicit))

    # 2. por _source
    source = analysis.get("_source")
    if isinstance(source, str) and source in SOURCE_CONFIDENCE:
        return SOURCE_CONFIDENCE[source]

    # 3. por intent
    intent = analysis.get("intent")
    if isinstance(intent, str) and intent in _INTENT_FALLBACK:
        return _INTENT_FALLBACK[intent]

    return _DEFAULT_CONFIDENCE


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))
