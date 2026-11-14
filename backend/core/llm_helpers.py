"""
llm_helpers — Llamadas directas a APIs de LLM.
Compartidos entre DiagnosticAgent y orchestrator._handle_reflect_command.
"""
import asyncio
import logging
import os
import time
from typing import Optional, Callable

from core.model_catalog import (
    ANTHROPIC_CLASSIFIER_THINKING_OFF,
    ANTHROPIC_MODELS,
    DEEPSEEK_FLASH_MODEL,
    DEEPSEEK_PRO_MODEL,
    DEEPSEEK_THINKING_OFF,
)

log = logging.getLogger("zephyro.llm_helpers")


def build_haiku_classifier(api_key: str, max_tokens: int = 10):
    """``ChatAnthropic`` para clasificadores cortos (SI/NO) sobre el tier ``fast``.

    Sin ``temperature`` (Haiku 5.5 devuelve 400 con un valor no-default) y con
    thinking apagado: ON por default, se comería ``max_tokens`` y la respuesta
    llegaría vacía.
    """
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=ANTHROPIC_MODELS["fast"],
        max_tokens=max_tokens,
        thinking=ANTHROPIC_CLASSIFIER_THINKING_OFF,
        api_key=api_key,
    )


def _track_usage(
    provider: str,
    model: str,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    success: bool = True,
    error: Optional[str] = None,
) -> None:
    """Registra una llamada directa (SDK nativo) en el api_usage_store. Nunca lanza."""
    try:
        from core.api_usage_store import get_usage_store, estimate_call_cost
        cost = estimate_call_cost(provider, model, tokens_in, tokens_out)
        get_usage_store().record(
            provider=provider, model=model,
            tokens_in=tokens_in, tokens_out=tokens_out,
            latency_ms=latency_ms, cost_usd=cost,
            success=success, error=error,
        )
    except Exception:
        pass


def _openai_usage(resp) -> tuple[int, int]:
    """(prompt_tokens, completion_tokens) de una respuesta OpenAI-compatible."""
    try:
        u = resp.usage
        return int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0)
    except Exception:
        return 0, 0


def _anthropic_usage(resp) -> tuple[int, int]:
    """(input_tokens, output_tokens) de una respuesta Anthropic."""
    try:
        u = resp.usage
        return int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0)
    except Exception:
        return 0, 0


async def call_deepseek_flash(prompt: str) -> Optional[str]:
    """DeepSeek Flash (V4.1) vía OpenAI-compatible API directa (sin OpenCode).

    Thinking apagado explícitamente: los callers envuelven esto en timeouts de
    3-8 s y el thinking default (effort high) no entra en esa ventana.
    """
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        _t0 = time.monotonic()
        resp = await client.chat.completions.create(
            model=DEEPSEEK_FLASH_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            extra_body=DEEPSEEK_THINKING_OFF,
        )
        _ti, _to = _openai_usage(resp)
        _track_usage("deepseek", DEEPSEEK_FLASH_MODEL, tokens_in=_ti, tokens_out=_to,
                     latency_ms=int((time.monotonic() - _t0) * 1000))
        return resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("call_deepseek_flash failed: %s", exc)
        return None


async def call_local_fast(prompt: str, max_tokens: int = 20, timeout: float = 4.0) -> Optional[str]:
    """Clasificación local rápida vía Ollama (modelo ZEPHYRO_MODEL_FAST).

    Para salidas CORTAS (intent de 1 palabra, SÍ/NO). Devuelve None si Ollama no está
    disponible, hay timeout o error → el caller cae a su clasificador cloud. Gateado por el
    circuit-breaker (ollama_health) para no pagar reintentos cuando Ollama está caído.
    """
    # Opt-in: en hardware débil (p.ej. GTX 1050 Ti 4GB) la inferencia local es MÁS LENTA e
    # inestable que el cloud para clasificar (medido: 18s local vs 6s cloud + casi-OOM). Por eso
    # el clasificador local es opt-in vía ZEPHYRO_LOCAL_CLASSIFIERS=1 (encenderlo con una GPU
    # holgada, p.ej. la RTX 5060 Ti 16GB). Default off → None → el caller usa cloud.
    if os.getenv("ZEPHYRO_LOCAL_CLASSIFIERS", "0").lower() not in ("1", "true", "yes", "on"):
        return None
    try:
        from core.ollama_health import is_ollama_up, mark_ollama_down
    except Exception:
        return None
    if not is_ollama_up():
        return None
    model = os.getenv("ZEPHYRO_MODEL_FAST", "llama3.2:1b")

    def _run() -> str:
        import ollama
        resp = ollama.generate(
            model=model,
            prompt=prompt,
            options={"temperature": 0, "num_predict": max_tokens},
        )
        return (resp.get("response") or "").strip()

    try:
        _t0 = time.monotonic()
        out = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
        _track_usage("ollama", model, latency_ms=int((time.monotonic() - _t0) * 1000))
        return out
    except Exception as exc:
        mark_ollama_down()
        log.warning("call_local_fast failed: %s", exc)
        return None


async def call_deepseek_flash_json(
    prompt: str,
    timeout: float = 8.0,
    max_tokens: int = 1024,
) -> Optional[str]:
    """DeepSeek Flash con JSON mode + timeout (thinking off). Para extractores que necesitan JSON estricto."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=timeout,
        )
        _t0 = time.monotonic()
        resp = await client.chat.completions.create(
            model=DEEPSEEK_FLASH_MODEL,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            extra_body=DEEPSEEK_THINKING_OFF,
        )
        _ti, _to = _openai_usage(resp)
        _track_usage("deepseek", DEEPSEEK_FLASH_MODEL, tokens_in=_ti, tokens_out=_to,
                     latency_ms=int((time.monotonic() - _t0) * 1000))
        return resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("call_deepseek_flash_json failed: %s", exc)
        return None


async def call_sonnet(prompt: str) -> Optional[str]:
    """Claude Sonnet (tier balanced del catálogo) vía Anthropic SDK directa."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    _model = ANTHROPIC_MODELS["balanced"]
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        _t0 = time.monotonic()
        resp = await client.messages.create(
            model=_model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        _ti, _to = _anthropic_usage(resp)
        _track_usage("anthropic", _model, tokens_in=_ti, tokens_out=_to,
                     latency_ms=int((time.monotonic() - _t0) * 1000))
        # Sonnet 5 corre thinking adaptativo: el primer bloque puede ser thinking.
        text_blocks = [b for b in resp.content if getattr(b, "type", "") == "text"]
        return text_blocks[0].text if text_blocks else ""
    except Exception as exc:
        log.warning("call_sonnet failed: %s", exc)
        return None


async def call_deepseek_pro(prompt: str) -> Optional[str]:
    """DeepSeek V4 Pro vía OpenAI-compatible API directa (sin OpenCode). Thinking default (razonamiento)."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        _t0 = time.monotonic()
        resp = await client.chat.completions.create(
            model=DEEPSEEK_PRO_MODEL,
            max_tokens=16384,
            messages=[{"role": "user", "content": prompt}],
        )
        _ti, _to = _openai_usage(resp)
        _track_usage("deepseek", DEEPSEEK_PRO_MODEL, tokens_in=_ti, tokens_out=_to,
                     latency_ms=int((time.monotonic() - _t0) * 1000))
        return resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("call_deepseek_pro failed: %s", exc)
        return None


async def call_opus_thinking(prompt: str) -> Optional[str]:
    """Claude Opus (tier power) con thinking adaptativo vía Anthropic SDK directa.

    ``budget_tokens`` fue removido en Claude 4.7+ (devuelve 400) — antes esta
    función fallaba siempre y devolvía None en silencio.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    _model = ANTHROPIC_MODELS["power"]
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        _t0 = time.monotonic()
        resp = await client.messages.create(
            model=_model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        _ti, _to = _anthropic_usage(resp)
        _track_usage("anthropic", _model, tokens_in=_ti, tokens_out=_to,
                     latency_ms=int((time.monotonic() - _t0) * 1000))
        text_blocks = [b for b in resp.content if b.type == "text"]
        return text_blocks[0].text if text_blocks else ""
    except Exception as exc:
        log.warning("call_opus_thinking failed: %s", exc)
        return None


async def call_with_fallback(
    prompt: str,
    llm_manager,
    *,
    preferred: Callable,
    tier_label: str = "",
) -> str:
    """Intenta `preferred` primero; si falla usa llm_manager con force_cloud."""
    result = await preferred(prompt)
    if result:
        return result
    log.warning("Preferred fn %s failed, usando llm_manager fallback", tier_label)
    return await asyncio.to_thread(llm_manager.generate, prompt, force_cloud=True)
