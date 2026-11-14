"""Providers router — gestión multi-provider via UI Tauri. Admin-only."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.auth_middleware import require_admin
from core.provider_config import (
    TASK_KINDS, get_provider_config, ProviderEntry,
)
from modules.code_analysis import binary_finder
from modules.code_analysis.opencode_bridge import get_opencode_bridge
from modules.code_analysis.opencode_config_writer import write_providers
from modules.code_analysis.pricing_table import estimate_cost

router = APIRouter()
log = logging.getLogger("zephyro.routers.providers")


# ── Models ─────────────────────────────────────────────────────────────

class ProviderUpdate(BaseModel):
    enabled: Optional[bool] = None
    default_model: Optional[str] = None
    models: Optional[list[str]] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = Field(default=None, description="Si se pasa, persiste en .env")


class TaskAssignment(BaseModel):
    provider: str
    model: Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/providers")
async def list_providers(_admin: dict = Depends(require_admin)) -> dict[str, Any]:
    cfg = get_provider_config()
    return {
        "providers": cfg.list_providers(),
        "task_assignment": cfg.task_assignments(),
        "task_kinds": list(TASK_KINDS),
        "binaries": {
            "opencode": binary_finder.find("opencode"),
            "claude": binary_finder.find("claude"),
            "gh": binary_finder.find("gh"),
            "npx": binary_finder.find("npx"),
        },
    }


# NOTA: las rutas /providers/usage* DEBEN declararse antes que /providers/{name},
# si no el path param {name} captura "usage" y nunca se alcanzan.
@router.get("/providers/usage")
async def usage_summary(
    period: str = "today",
    _admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Uso real por provider en el periodo dado (today | week | month | all),
    leído del api_usage_store (tokens, costo estimado, latencia, requests).
    """
    from core.api_usage_store import get_usage_store
    if period not in ("today", "week", "month", "all"):
        period = "today"
    stats = get_usage_store().get_stats(period)
    totals = {
        "requests": sum(s["requests"] for s in stats.values()),
        "tokens_in": sum(s["tokens_in"] for s in stats.values()),
        "tokens_out": sum(s["tokens_out"] for s in stats.values()),
        "cost_usd": round(sum(s["cost_usd"] for s in stats.values()), 6),
    }
    return {"period": period, "stats": stats, "totals": totals}


@router.get("/providers/usage/log")
async def usage_log(
    provider: Optional[str] = None,
    limit: int = 50,
    _admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Últimas llamadas registradas, opcionalmente filtradas por provider."""
    from core.api_usage_store import get_usage_store
    limit = max(1, min(limit, 200))
    return {"entries": get_usage_store().get_log(provider=provider, limit=limit)}


@router.get("/providers/{name}")
async def get_provider(
    name: str, _admin: dict = Depends(require_admin)
) -> dict[str, Any]:
    cfg = get_provider_config()
    entry = cfg.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Provider {name!r} no encontrado")
    return entry.to_dict()


@router.post("/providers/{name}/configure")
async def configure_provider(
    name: str,
    body: ProviderUpdate,
    _admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    cfg = get_provider_config()
    try:
        entry = cfg.update(
            name,
            enabled=body.enabled,
            default_model=body.default_model,
            models=body.models,
            base_url=body.base_url,
            api_key=body.api_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Si el provider usa OpenCode, regenerar opencode.jsonc con todos los
    # providers opencode_cli activos.
    if entry.backend == "opencode_cli":
        try:
            opencode_providers = []
            for prov_dict in cfg.list_providers():
                e = cfg.get(prov_dict["name"])
                if e and e.backend == "opencode_cli" and e.enabled:
                    opencode_providers.append({
                        "name": e.name,
                        "base_url": e.base_url,
                        "api_key_env": e.api_key_env,
                        "models": e.models,
                    })
            cfg_path = write_providers(opencode_providers)
            log.info("[providers] opencode.jsonc regenerado: %s", cfg_path)
        except Exception as exc:
            log.warning("[providers] no pude regenerar opencode.jsonc: %s", exc)

    return entry.to_dict()


@router.post("/providers/{name}/test")
async def test_provider(
    name: str, _admin: dict = Depends(require_admin)
) -> dict[str, Any]:
    """
    Prueba un provider con una llamada minima. Devuelve {ok, latency_ms, error}.
    """
    cfg = get_provider_config()
    entry = cfg.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Provider {name!r} no existe")

    start = time.monotonic()
    ok, error = False, None
    try:
        if entry.backend == "anthropic_sdk":
            ok, error = await _test_anthropic(entry)
        elif entry.backend == "opencode_cli":
            ok, error = await _test_opencode(entry)
        elif entry.backend == "claude_cli":
            ok, error = _test_claude_cli(entry)
        elif entry.backend == "ollama":
            ok, error = await _test_ollama(entry)
        elif entry.backend in ("openai_sdk", "google_sdk"):
            ok, error = True, "test omitido (delegado al runtime)"
        else:
            error = f"backend {entry.backend!r} sin handler de test"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    latency_ms = int((time.monotonic() - start) * 1000)
    cfg.record_test_result(name, ok, error)

    return {"ok": ok, "latency_ms": latency_ms, "error": error, "provider": name}


@router.post("/providers/{name}/disable")
async def disable_provider(
    name: str, _admin: dict = Depends(require_admin)
) -> dict[str, Any]:
    cfg = get_provider_config()
    cfg.disable(name)
    return {"name": name, "enabled": False}


@router.post("/providers/tasks/{task}/assign")
async def assign_task(
    task: str, body: TaskAssignment, _admin: dict = Depends(require_admin)
) -> dict[str, Any]:
    cfg = get_provider_config()
    try:
        cfg.assign_task(task, provider=body.provider, model=body.model)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return cfg.task_assignments()


# ── Backend-specific test helpers ──────────────────────────────────────

async def _test_anthropic(entry: ProviderEntry) -> tuple[bool, Optional[str]]:
    api_key = os.getenv(entry.api_key_env or "")
    if not api_key:
        return False, "ANTHROPIC_API_KEY no seteada"
    try:
        from anthropic import AsyncAnthropic
        from core.model_catalog import ANTHROPIC_MODELS
        client = AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model=entry.default_model or ANTHROPIC_MODELS["fast"],
            # Margen para el thinking adaptativo de los modelos 5.x.
            max_tokens=64,
            messages=[{"role": "user", "content": "ping"}],
        )
        return bool(resp.content), None
    except Exception as exc:
        return False, str(exc)[:200]


async def _test_opencode(entry: ProviderEntry) -> tuple[bool, Optional[str]]:
    if entry.api_key_env and not os.getenv(entry.api_key_env):
        return False, f"{entry.api_key_env} no seteada"
    bridge = get_opencode_bridge()
    if not await bridge.is_available():
        return False, "opencode CLI no instalado (npm i -g opencode-ai)"
    # Test mínimo
    result = await bridge.query(
        "Responde con la palabra 'pong' nada más.",
        model=entry.default_model,
        timeout=30.0,
    )
    if result.ok and result.text:
        return True, None
    return False, result.error or "respuesta vacía"


def _test_claude_cli(entry: ProviderEntry) -> tuple[bool, Optional[str]]:
    binary = binary_finder.find("claude")
    if binary is None:
        return False, "claude CLI no instalado"
    return True, None  # Solo verificamos que el binario existe


async def _test_ollama(entry: ProviderEntry) -> tuple[bool, Optional[str]]:
    import aiohttp
    base = entry.base_url or "http://localhost:11434"
    try:
        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base}/api/tags") as resp:
                if resp.status == 200:
                    return True, None
                return False, f"HTTP {resp.status}"
    except Exception as exc:
        return False, str(exc)[:200]
