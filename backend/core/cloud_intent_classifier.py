"""
cloud_intent_classifier — Classifier registry-aware para cloud mode (Fase 2).

En cloud mode el router corre con ``skip_llm=True``: la clasificación queda 100%
heurística y la cola larga del lenguaje ("fijate qué tengo encima esta semana",
"anotame que el viernes rindo") cae al chat, donde el LLM alucina capacidades.

Este classifier es el último escalón ANTES del chat: recibe el catálogo DINÁMICO
de skills del registry (``registry.get_prompt_summaries()``) y decide qué lo
maneja. Como lee el catálogo en runtime, auto-descubre skills nuevas sin tocar
este archivo. Reusa el motor LLM ya probado del extractor de browser
(``_call_extractor_llm`` → DeepSeek Flash JSON → fallback Haiku, ~30x más barato).

Always-on en cloud: corre sobre el residual (lo que ya falló heurística de skills
Y browser-extract). La charla casual igual lo invoca, pero devuelve route="chat"
y el pipeline sigue de largo.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from core.browser_intent_schema import _call_extractor_llm, _clean_llm_json

log = logging.getLogger(__name__)


_CLASSIFIER_PROMPT = """\
Sos un clasificador de intenciones de un asistente personal. Te doy el mensaje \
del usuario y el catálogo de HABILIDADES (skills) disponibles. Decidí qué \
componente debe manejar el mensaje.

HABILIDADES DISPONIBLES:
{catalog}

Además existe "automation" (abrir apps, navegar webs, reproducir media) y "chat" \
(conversación general, preguntas, saludos — cuando NINGUNA habilidad aplica).

Reglas:
- Elegí "skill:<nombre>" SOLO si el mensaje claramente cae en lo que esa \
habilidad hace (usá el nombre EXACTO del catálogo).
- "automation" si pide abrir/navegar/reproducir algo.
- "chat" si es charla, una pregunta general, o no estás seguro.
- confidence: 0.0 a 1.0, qué tan seguro estás del route.

Respondé SOLO con JSON válido, sin markdown ni explicaciones:
{{"route": "skill:<nombre>" | "automation" | "chat", "confidence": <float>, "target": "<texto relevante o null>"}}

Mensaje del usuario: {user_input}"""


def _parse_classification(raw: str, valid_skills: set[str]) -> Optional[dict[str, Any]]:
    """Parsea el JSON del classifier de forma tolerante. Valida el route contra
    las skills reales del catálogo: si el LLM inventa una skill inexistente, se
    degrada a chat (None). Devuelve un analysis-dict o None (= chat / no-acción).
    """
    cleaned = _clean_llm_json(raw)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    route = str(data.get("route", "chat")).strip()
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    target = data.get("target")
    if target in ("null", "None", ""):
        target = None

    if route == "automation":
        return {
            "intent": "automation",
            "target": str(target) if target else "",
            "_source": "capa_cloud_classifier",
            "confidence": confidence,
        }

    if route.startswith("skill:"):
        skill_name = route.split(":", 1)[1].strip()
        # Validar contra el catálogo real — no inventar skills.
        if skill_name not in valid_skills:
            log.debug("[classifier] route a skill inexistente %r → chat", skill_name)
            return None
        return {
            "intent": "skill",
            "skill_name": skill_name,
            "_source": "capa_cloud_classifier",
            "confidence": confidence,
            "target": str(target) if target else None,
        }

    # route == "chat" o cualquier otra cosa → no-acción (cae al chat).
    return None


def _skill_names_from_catalog(skill_catalog: str) -> set[str]:
    """Extrae los nombres de skill del catálogo `- nombre: descripción`."""
    names: set[str] = set()
    for line in skill_catalog.splitlines():
        m = re.match(r"\s*-\s*([^:]+):", line)
        if m:
            names.add(m.group(1).strip())
    return names


async def classify_intent(
    user_input: str,
    llm_manager: Any,
    skill_catalog: str,
) -> Optional[dict[str, Any]]:
    """Clasifica la intención de ``user_input`` contra el catálogo de skills.

    Devuelve un analysis-dict con ``intent`` ("skill"|"automation"),
    ``_source="capa_cloud_classifier"`` y ``confidence``; o None si el route es
    chat / skill inexistente / el LLM no está disponible / falló el parseo.
    Nunca levanta excepción hacia arriba (fallback graceful → None → chat).
    """
    if not skill_catalog or not skill_catalog.strip():
        return None
    if not hasattr(llm_manager, "generate") and not hasattr(llm_manager, "_call_cloud"):
        return None

    valid_skills = _skill_names_from_catalog(skill_catalog)
    prompt = _CLASSIFIER_PROMPT.format(catalog=skill_catalog, user_input=user_input)
    try:
        raw = await _call_extractor_llm(llm_manager, prompt)
    except Exception as exc:  # noqa: BLE001 — graceful, cae a chat
        log.debug("[classifier] _call_extractor_llm falló: %s", exc)
        return None
    if not raw:
        return None
    return _parse_classification(raw, valid_skills)
