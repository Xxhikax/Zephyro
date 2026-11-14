"""
model_catalog — fuente única de verdad de los IDs de modelo cloud.

Mismo patrón que ``core/app_config.py`` para app-routing: cada módulo que necesite
un ID de modelo lo IMPORTA de acá en vez de repetir el literal. Cuando un provider
retira un modelo (DeepSeek retiró ``deepseek-chat`` en 2026-07; Anthropic rota
generaciones cada ~6 meses) se cambia en UN solo lugar.

Estado verificado el 2026-10-07 contra docs oficiales:
- DeepSeek: ``deepseek-flash`` (V4.1-Flash) y ``deepseek-v4-pro`` son los únicos
  IDs vigentes. ``deepseek-v4-flash`` todavía se acepta pero su modelo fue
  retirado (lo sirve V4.1-Flash). ``deepseek-chat``/``deepseek-reasoner`` ya no
  existen. Thinking viene ON por default (effort high) y en ese modo
  ``temperature`` no tiene efecto → los paths con timeout corto lo apagan.
- Anthropic: Haiku 5.5 / Sonnet 5.5 / Opus 5.5 / Fable 5.1 son la generación
  vigente. En todos, ``temperature``/``top_p``/``top_k`` no-default → 400 y
  ``thinking.budget_tokens`` → 400. Los cuatro piensan (adaptive) por default:

  | modelo      | thinking {"type": "disabled"}          |
  |-------------|----------------------------------------|
  | Haiku 5.5   | OK (apaga thinking con effort <= high) |
  | Sonnet 5.5  | 400 (lo mínimo es "between_tools")     |
  | Opus 5.5    | 400 (thinking siempre ON)              |
  | Fable 5.1   | 400 (thinking siempre ON)              |

  Por eso los clasificadores con ``max_tokens`` chico usan SOLO el tier
  ``fast`` con ``ANTHROPIC_CLASSIFIER_THINKING_OFF``: sin eso el thinking se
  come los tokens y la respuesta llega vacía, y el primer bloque de
  ``content`` es ``thinking`` (no ``text``).
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("zephyro.model_catalog")

# ── DeepSeek (OpenAI-compatible, base_url https://api.deepseek.com) ──────────
DEEPSEEK_CHAT_MODEL = "deepseek-flash"      # N3 chat: rápido, thinking OFF
DEEPSEEK_FLASH_MODEL = "deepseek-flash"     # helpers/clasificadores, thinking OFF
DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"      # razonamiento pesado, thinking default

# ``extra_body`` para apagar el thinking (default ON en V4.1). Sin esto, los
# clasificadores con timeout de 3-8 s revientan y ``temperature`` es rechazado.
DEEPSEEK_THINKING_OFF: dict = {"thinking": {"type": "disabled"}}

# Modelos elegibles para el chat N3 (selector MODEL_ROUTING de la UI). Los
# helpers ``call_deepseek_flash*``/``call_deepseek_pro`` NO leen esta selección:
# usan las constantes de arriba, así el router/clasificadores no cambian.
DEEPSEEK_MODELS: dict[str, str] = {
    "flash": DEEPSEEK_FLASH_MODEL,
    "pro": DEEPSEEK_PRO_MODEL,
}
DEEPSEEK_DEFAULT_MODEL = DEEPSEEK_MODELS["flash"]
DEEPSEEK_VALID_MODELS: list[str] = list(DEEPSEEK_MODELS.values())
DEEPSEEK_LEGACY_MODELS: dict[str, str] = {
    "deepseek-v4-flash": DEEPSEEK_MODELS["flash"],
    "deepseek-chat": DEEPSEEK_MODELS["flash"],
    "deepseek-reasoner": DEEPSEEK_MODELS["pro"],
}

# ── Anthropic ────────────────────────────────────────────────────────────────
ANTHROPIC_MODELS: dict[str, str] = {
    "fast": "claude-haiku-5-5",
    "balanced": "claude-sonnet-5-5",
    "power": "claude-opus-5-5",
    "frontier": "claude-fable-5-1",
}
ANTHROPIC_DEFAULT_MODEL = ANTHROPIC_MODELS["balanced"]
ANTHROPIC_VALID_MODELS: list[str] = list(ANTHROPIC_MODELS.values())

# Valor de ``thinking`` para clasificadores sobre ANTHROPIC_MODELS["fast"].
# Solo Haiku lo acepta; con Sonnet/Opus/Fable devuelve 400 (ver tabla arriba).
ANTHROPIC_CLASSIFIER_THINKING_OFF: dict = {"type": "disabled"}

# IDs que Zephyro usó antes y que todavía pueden estar en ``.env``,
# ``storage/providers.json`` o en la UI de un cliente viejo. Se remapean al tier
# equivalente vigente en vez de rechazarlos.
ANTHROPIC_LEGACY_MODELS: dict[str, str] = {
    "claude-sonnet-4-6": ANTHROPIC_MODELS["balanced"],
    "claude-sonnet-5": ANTHROPIC_MODELS["balanced"],
    "claude-opus-4-6": ANTHROPIC_MODELS["power"],
    "claude-opus-4-7": ANTHROPIC_MODELS["power"],
    "claude-opus-5": ANTHROPIC_MODELS["power"],
    "claude-fable-5": ANTHROPIC_MODELS["frontier"],
    "claude-haiku-4-5": ANTHROPIC_MODELS["fast"],
    "claude-haiku-4-5-20251001": ANTHROPIC_MODELS["fast"],
}

# Aliases cortos usados por Telegram ``/model`` y el CLI.
ANTHROPIC_ALIASES: dict[str, str] = {
    "haiku": ANTHROPIC_MODELS["fast"],
    "sonnet": ANTHROPIC_MODELS["balanced"],
    "opus": ANTHROPIC_MODELS["power"],
    "fable": ANTHROPIC_MODELS["frontier"],
}

# ── Otros providers (sin cambio, solo centralizados) ─────────────────────────
GEMINI_CHAT_MODEL = "gemini-2.5-flash-lite"
OPENAI_CHAT_MODEL = "gpt-4o-mini"


def resolve_anthropic_model(model: Optional[str]) -> Optional[str]:
    """
    Normaliza un ID de modelo Anthropic.

    - ID vigente → se devuelve igual.
    - ID legacy conocido → se remapea al tier vigente (con warning).
    - Cualquier otra cosa (incluido vacío) → ``None``; el caller decide si usar
      el default o rechazar.
    """
    if not model:
        return None
    model = model.strip()
    if model in ANTHROPIC_VALID_MODELS:
        return model
    replacement = ANTHROPIC_LEGACY_MODELS.get(model)
    if replacement:
        log.warning("[model_catalog] %s es legacy -> usando %s (actualizá ANTHROPIC_MODEL en .env)",
                    model, replacement)
        return replacement
    return None


def resolve_deepseek_model(model: Optional[str]) -> Optional[str]:
    """Igual que ``resolve_anthropic_model`` pero para los modelos de chat DeepSeek."""
    if not model:
        return None
    model = model.strip()
    if model in DEEPSEEK_VALID_MODELS:
        return model
    replacement = DEEPSEEK_LEGACY_MODELS.get(model)
    if replacement:
        log.warning("[model_catalog] %s es legacy -> usando %s (actualizá DEEPSEEK_MODEL en .env)",
                    model, replacement)
        return replacement
    return None


def cloud_chat_model(provider: Optional[str]) -> Optional[str]:
    """ID del modelo de chat N3 DEFAULT para un provider (None si no es cloud conocido).

    Para DeepSeek el modelo activo puede ser otro (``LLMManager.deepseek_model``).
    """
    return {
        "gemini": GEMINI_CHAT_MODEL,
        "deepseek": DEEPSEEK_CHAT_MODEL,
        "openai": OPENAI_CHAT_MODEL,
    }.get(provider or "")
