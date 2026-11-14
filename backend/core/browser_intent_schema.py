"""Browser Intent Extractor — LLM-based fallback for ambiguous browser commands.

When the regex heuristics in router.py fail to classify a browser/automation
command, this module uses Claude Haiku with structured JSON output to extract
the user's intent and parameters in a single call.

Unlike the regex system (which requires patching for every new phrasing), the
LLM understands natural language directly and maps it to a structured schema.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

log = logging.getLogger("zephyro.intent_extractor")

# ── JSON Schema (Anthropic-format tool definition) ──────────────────────

BROWSER_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "navigate",
                "search",
                "play_media",
                "click",
                "type",
                "scroll",
                "screenshot",
                "extract_text",
                "playback_control",
                "sequence",
                "none",
            ],
            "description": "La acción de navegador que el usuario quiere realizar. "
                           "'navigate' = abrir URL o app. 'search' = buscar en web o plataforma. "
                           "'play_media' = reproducir música/video. 'click' = hacer clic. "
                           "'type' = escribir texto. 'scroll' = desplazar. "
                           "'screenshot' = capturar pantalla. 'extract_text' = leer/extraer contenido. "
                           "'playback_control' = pausar/reanudar/siguiente/anterior. "
                           "'sequence' = lista ordenada de 2+ pasos atómicos cuando el usuario "
                           "encadena instrucciones con 'y luego', 'después', etc. Usar 'steps' array. "
                           "'none' = no es un comando de navegador.",
        },
        "target": {
            "type": "string",
            "description": "El objetivo: URL, consulta de búsqueda, nombre de app, "
                           "canción/artista, selector CSS, texto del botón, etc.",
        },
        "provider": {
            "type": "string",
            "enum": ["google", "youtube", "youtube_music", "spotify", "github", "netflix", "twitter", "reddit", "wikipedia", "generic"],
            "description": "Plataforma/proveedor donde realizar la acción.",
        },
        "params": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Selector CSS o texto del elemento para click/type.",
                },
                "text": {
                    "type": "string",
                    "description": "Texto a escribir en el campo (solo para 'type').",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Dirección de scroll.",
                },
                "amount": {
                    "type": "integer",
                    "description": "Cantidad de píxeles de scroll (default: 300).",
                },
            },
        },
        "steps": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Lista ordenada de pasos cuando action='sequence'. Cada step "
                           "es un sub-objeto con action/target/provider/params propios. "
                           "Por ejemplo, [{'action':'play_media','target':'thunderstruck','provider':'youtube_music'},"
                           "{'action':'navigate','target':'discord','provider':'generic'}].",
        },
    },
    "required": ["action"],
}

# Prompt optimizado para Haiku — extrae intención de navegador en un solo JSON
_BROWSER_EXTRACTION_PROMPT = """Eres un clasificador de intenciones de navegador. Analiza el mensaje del usuario y devuelve SOLO un JSON válido con la acción de navegador que corresponde.

Reglas:
- Si NO es un comando de navegador/automatización, usa action="none"
- Si es ambiguo entre varias acciones, elige la más probable
- CRÍTICO — Quejas conversacionales: si el usuario se está QUEJANDO de que algo no se ejecutó ("no llegaste a abrir", "no abriste nada", "que no abriste X", "por qué no pudiste", "no conseguiste", "no llegaste a hacer"), responde SIEMPRE con action="none". NO conviertas la queja en un comando nuevo.
  Ejemplos:
  - "no llegaste a abrir nada" → {"action":"none"}
  - "que no me abriste discord" → {"action":"none"}
  - "no abriste youtube music" → {"action":"none"}
  - "qué pasó? no se abrió nada" → {"action":"none"}
- Para búsquedas: action="search", target=<query>, provider=<google|youtube|etc>
- Para abrir URLs/apps: action="navigate", target=<url o nombre de app>
- Para música/video: action="play_media", target=<canción/artista>, provider=<youtube|spotify|youtube_music>
- Para interacciones en página: action="click"/"type"/"scroll" con params
- Para capturas: action="screenshot"
- Para controles de reproducción: action="playback_control", target=<pause|resume|next|prev|mute|close>
- Para 2+ instrucciones encadenadas con "y luego", "después", "y abre", etc., usa action="sequence" con un array "steps" donde cada step tiene su propio action/target/provider.

IMPORTANTE — Detección de instrucciones compuestas:
Si el input contiene CUALQUIERA de estos conectores: "y luego", "y después", "después", "luego", "primero...luego", "primero...y", "y abre", "y reproduce", "y busca" → DEBES usar action="sequence" con TODOS los pasos. NUNCA colapses a una sola acción.

Ejemplos de sequence:
- "reproduce Thunderstruck en youtube music y luego abres discord" →
  {"action":"sequence","steps":[
    {"action":"play_media","target":"Thunderstruck","provider":"youtube_music"},
    {"action":"navigate","target":"discord","provider":"generic"}
  ]}
- "abre google y busca lobos del rio" →
  {"action":"sequence","steps":[
    {"action":"navigate","target":"google","provider":"google"},
    {"action":"search","target":"lobos del rio","provider":"google"}
  ]}
- "abre calculadora y luego youtube music" →
  {"action":"sequence","steps":[
    {"action":"navigate","target":"calculadora","provider":"generic"},
    {"action":"navigate","target":"music.youtube.com","provider":"youtube_music"}
  ]}
- "primero quiero que abras calculadora y luego abras YouTube Music en el navegador" →
  {"action":"sequence","steps":[
    {"action":"navigate","target":"calculadora","provider":"generic"},
    {"action":"navigate","target":"music.youtube.com","provider":"youtube_music"}
  ]}

El usuario habla en español. Devuelve SOLO el JSON, sin explicaciones ni markdown."""


def _clean_llm_json(raw: str) -> str:
    """Extrae el JSON del output del LLM, descartando markdown y texto sobrante."""
    # Quitar bloques de código markdown ```json ... ```
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if m:
        return m.group(1)
    # Buscar primer { y último }
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1 and end > start:
        return raw[start:end + 1]
    return raw.strip()


def parse_browser_action(response_json: str) -> Optional[dict[str, Any]]:
    """Parsea la respuesta JSON del LLM y la convierte al formato del router.

    Si action='sequence', parsea recursivamente cada step y devuelve un dict
    con automation_intent='SEQUENCE' y steps=[lista de sub-results].
    """
    cleaned = _clean_llm_json(response_json)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    action = data.get("action", "none")
    if action == "none":
        return None

    # ── Sequence: parsea cada step recursivamente ─────────────────────────
    if action == "sequence":
        steps_raw = data.get("steps", []) or []
        parsed_steps: list[dict[str, Any]] = []
        for step in steps_raw:
            if not isinstance(step, dict):
                continue
            sub = parse_browser_action(json.dumps(step))
            if sub:
                parsed_steps.append(sub)
        if not parsed_steps:
            return None
        return {
            "intent": "automation",
            "automation_intent": "SEQUENCE",
            "steps": parsed_steps,
            "_source": "capa1.5_llm_extractor_sequence",
        }

    target = data.get("target", "")
    provider = data.get("provider", "generic")
    params = data.get("params", {}) or {}

    # Mapeo al formato que espera el router/orchestrator
    result: dict[str, Any] = {
        "intent": "automation",
        "target": str(target),
        "_source": "capa1.5_llm_extractor",
    }

    if action == "navigate":
        result["automation_intent"] = "NAVIGATE"
    elif action == "search":
        result["automation_intent"] = "SEARCH"
        result["provider"] = provider
        result["search_query"] = str(target)
    elif action == "play_media":
        result["automation_intent"] = "PLAY_MEDIA"
        result["provider"] = provider if provider != "generic" else "youtube"
        result["search_query"] = str(target)
    elif action in ("click", "type", "scroll", "screenshot", "extract_text"):
        result["automation_intent"] = "INTERACT"
        result["interact_action"] = action
        result["interact_target"] = str(target)
        if action == "type":
            result["interact_text"] = params.get("text", "")
        elif action == "scroll":
            result["scroll_direction"] = params.get("direction", "down")
            result["scroll_amount"] = params.get("amount", 300)
    elif action == "playback_control":
        result["automation_intent"] = "PLAYBACK_CONTROL"
        ctl_map = {
            "pause": "pause", "resume": "resume", "next": "next",
            "prev": "prev", "mute": "mute", "unmute": "unmute",
            "close": "close", "stop": "close",
        }
        result["control_action"] = ctl_map.get(str(target).lower(), str(target).lower())

    # Requerir confirmación cuando la confianza es baja:
    # - Provider genérico en play_media/search (el LLM no identificó la plataforma)
    # - Target muy corto (posible extracción incompleta)
    needs_conf = False
    if action in ("play_media", "search") and provider == "generic":
        needs_conf = True
    if action in ("play_media", "navigate") and len(target) < 5:
        needs_conf = True
    if needs_conf:
        result["needs_confirmation"] = True
        result["_confidence"] = 0.6

    return result


async def extract_browser_intent(
    user_input: str,
    llm_manager: Any,
) -> Optional[dict[str, Any]]:
    """Usa LLM (DeepSeek V4 Flash con fallback a Haiku) para extraer la intención
    de navegador del input.

    Solo se llama cuando los regex heurísticos de Layer 1 no encontraron
    una acción de automatización. Retorna un dict compatible con el router,
    o None si el LLM decide que no es un comando de navegador.
    """
    if not hasattr(llm_manager, '_call_cloud') and not hasattr(llm_manager, 'generate'):
        return None

    try:
        prompt = f"{_BROWSER_EXTRACTION_PROMPT}\n\nMensaje del usuario: {user_input}"
        raw = await _call_extractor_llm(llm_manager, prompt)
        if not raw:
            return None
        return parse_browser_action(raw)
    except Exception as exc:
        log.debug("[intent] extract_browser_intent failed: %s", exc)
        return None


async def _call_extractor_llm(llm_manager: Any, prompt: str) -> str:
    """
    Routea la extracción de intent:
    - Primero intenta DeepSeek V4 Flash (más barato, ~30x menos que Haiku).
    - Fallback a Haiku via llm_manager.generate() si DeepSeek falla.

    Override por env: BROWSER_INTENT_PROVIDER=deepseek|anthropic|auto (default: auto).
    """
    import asyncio

    provider_override = os.getenv("BROWSER_INTENT_PROVIDER", "auto").lower()
    full_prompt = (
        "Eres un clasificador de intenciones. Responde SOLO con JSON válido, "
        "sin markdown ni explicaciones.\n\n" + prompt
    )

    # Intento 1: DeepSeek V4 Flash (cuando provider=auto o deepseek + key disponible)
    if provider_override in ("auto", "deepseek") and os.getenv("DEEPSEEK_API_KEY"):
        try:
            from core.llm_helpers import call_deepseek_flash_json
            result = await call_deepseek_flash_json(full_prompt, timeout=8.0)
            if result:
                log.debug("[intent] DeepSeek Flash OK (len=%d)", len(result))
                return result
            log.debug("[intent] DeepSeek Flash devolvió vacío, fallback a Haiku")
        except Exception as exc:
            log.warning("[intent] DeepSeek Flash error, fallback a Haiku: %s", exc)

    # Si el override es 'deepseek' explícito y falló, no caer a Haiku
    if provider_override == "deepseek":
        return ""

    # Fallback: Haiku via llm_manager.generate()
    if hasattr(llm_manager, "generate"):
        return await asyncio.to_thread(
            lambda: llm_manager.generate(
                full_prompt, force_cloud=True, force_local=False,
            )
        )
    return ""
