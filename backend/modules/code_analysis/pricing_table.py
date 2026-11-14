"""
Tabla local de pricing por provider/modelo (USD por 1M tokens).

ÚLTIMA ACTUALIZACIÓN: 2026-10-07 (Anthropic 5.5 / Fable 5.1)
Fuentes:
    - Anthropic: https://www.anthropic.com/pricing
    - DeepSeek: https://api-docs.deepseek.com/quick_start/pricing
    - OpenRouter: https://openrouter.ai/models
    - OpenAI: https://openai.com/api/pricing/
    - Google: https://ai.google.dev/pricing

Estos valores son aproximados. Para facturación exacta consultar el
dashboard del provider correspondiente.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float   # USD por 1M tokens de input
    output_per_million: float  # USD por 1M tokens de output
    cache_read_per_million: float = 0.0
    cache_write_per_million: float = 0.0


# Diccionario plano: "provider/model" → ModelPricing
PRICING: dict[str, ModelPricing] = {
    # Anthropic — generación vigente (2026-10, precios API first-party).
    # Haiku 5.5: tramo <= 100k tokens de prompt (arriba de eso cuesta 5x).
    "anthropic/claude-haiku-5-5":   ModelPricing(0.10,  0.50, 0.01, 0.125),
    "anthropic/claude-sonnet-5-5":  ModelPricing(2.00, 10.00, 0.10, 2.50),
    "anthropic/claude-opus-5-5":    ModelPricing(4.00, 20.00, 0.20, 5.00),
    "anthropic/claude-fable-5-1":   ModelPricing(10.00, 50.00, 0.25, 12.50),
    # Anthropic — legacy (se conservan para logs históricos de api_usage_store)
    "anthropic/claude-haiku-4-5":   ModelPricing(1.00,  5.00, 0.10, 1.25),
    "anthropic/claude-sonnet-5":    ModelPricing(2.00, 10.00, 0.20, 2.50),
    "anthropic/claude-opus-5":      ModelPricing(5.00, 25.00, 0.50, 6.25),
    "anthropic/claude-sonnet-4-6":  ModelPricing(3.00, 15.00, 0.30, 3.75),
    "anthropic/claude-opus-4-7":    ModelPricing(5.00, 25.00, 0.50, 6.25),

    # DeepSeek — precios PEAK oficiales (conservador; off-peak es ~50% menos).
    # "deepseek-flash" = API directa (V4.1-Flash); "deepseek-v4-flash" = naming
    # OpenCode, mismo modelo por ruteo de compatibilidad.
    "deepseek/deepseek-flash":      ModelPricing(0.30, 1.20, 0.006),
    "deepseek/deepseek-v4-flash":   ModelPricing(0.30, 1.20, 0.006),
    "deepseek/deepseek-v4-pro":     ModelPricing(1.32, 3.96, 0.044),
    "deepseek/deepseek-r1":         ModelPricing(0.55, 2.19),  # retirado, solo histórico

    # OpenAI
    "openai/gpt-4o":                ModelPricing(2.50, 10.00),
    "openai/gpt-4o-mini":           ModelPricing(0.15, 0.60),

    # Google Gemini
    "gemini/gemini-2.5-flash":      ModelPricing(0.075, 0.30),
    "gemini/gemini-2.5-pro":        ModelPricing(1.25, 5.00),

    # Local
    "ollama_local/llama3.2:1b":     ModelPricing(0.0, 0.0),
    "ollama_local/llama3.2:3b":     ModelPricing(0.0, 0.0),

    # Claude CLI hereda Pro/Max — costo por suscripción, no por token
    "claude_cli/claude-sonnet-5-5": ModelPricing(0.0, 0.0),
    "claude_cli/claude-opus-5-5":   ModelPricing(0.0, 0.0),
    "claude_cli/claude-sonnet-5":   ModelPricing(0.0, 0.0),
    "claude_cli/claude-opus-5":     ModelPricing(0.0, 0.0),
    "claude_cli/claude-sonnet-4-6": ModelPricing(0.0, 0.0),
    "claude_cli/claude-opus-4-7":   ModelPricing(0.0, 0.0),
}


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """
    Estima el costo en USD de una llamada. Si el modelo no está en la tabla,
    devuelve 0 (no asumir costos desconocidos).
    """
    key = f"{provider}/{model}"
    p = PRICING.get(key)
    if p is None:
        # Fallback: buscar solo por provider con primer modelo conocido
        for k, val in PRICING.items():
            if k.startswith(f"{provider}/"):
                p = val
                break
    if p is None:
        return 0.0

    return (
        (input_tokens / 1_000_000.0) * p.input_per_million
        + (output_tokens / 1_000_000.0) * p.output_per_million
        + (cache_read_tokens / 1_000_000.0) * p.cache_read_per_million
        + (cache_write_tokens / 1_000_000.0) * p.cache_write_per_million
    )


def list_supported() -> list[str]:
    return sorted(PRICING.keys())
