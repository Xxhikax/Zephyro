"""
PreconditionGuards — Guards síncronos evaluados ANTES de cualquier acción.

Cada guard debe ejecutarse en < 50ms. Sin I/O bloqueante: usar shutil.which()
en lugar de subprocess.run() para verificar binarios.

Uso:
    results = run_guards("temporal_search", {"user_input": "accidentes hoy"})
    for r in results:
        if not r.passed:
            # bloquear o notificar
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass
class GuardResult:
    """Resultado de evaluar un guard."""
    passed: bool
    reason: str = ""
    guard_name: str = ""
    suggested_fix: Optional[str] = None
    enrichment: dict = field(default_factory=dict)  # datos para inyectar al LLM


# ── Guards individuales ───────────────────────────────────────────────────────

def verify_current_date(context: dict) -> GuardResult:
    """
    Siempre pasa. Inyecta la fecha/hora verificada del servidor en el contexto.
    Solución al bug: LLM asumía fecha de artículos buscados en lugar del reloj real.
    """
    now = datetime.now()
    return GuardResult(
        passed=True,
        guard_name="verify_current_date",
        enrichment={
            "verified_date": now.strftime("%Y-%m-%d"),
            "verified_time": now.strftime("%H:%M"),
        },
    )


_TEMPORAL_KEYWORDS = [
    "hoy", "ahora", "ayer", "esta semana", "este mes", "este año",
    "esta mañana", "esta tarde", "esta noche", "reciente", "recientes",
    "actual", "actualmente", "últimas noticias", "últimos días",
    "today", "now", "yesterday", "recent", "latest", "current",
    "this week", "this month", "this year", "breaking",
]


def check_source_recency(context: dict) -> GuardResult:
    """
    Detecta queries temporales (hoy/now/reciente/etc.) y enriquece con fecha verificada.
    No bloquea — solo inyecta contexto para que el LLM compare fechas correctamente.
    """
    user_input = context.get("user_input", "").lower()
    has_temporal = any(kw in user_input for kw in _TEMPORAL_KEYWORDS)
    if has_temporal:
        now = datetime.now()
        return GuardResult(
            passed=True,
            guard_name="check_source_recency",
            reason="Query temporal detectada — fecha del servidor inyectada",
            enrichment={
                "has_temporal_intent": True,
                "verified_date": now.strftime("%Y-%m-%d"),
            },
        )
    return GuardResult(passed=True, guard_name="check_source_recency")


def check_skill_health(context: dict) -> GuardResult:
    """
    Consulta LivingState para verificar la salud de una skill antes de invocarla.
    Bloquea si está `broken`, advierte (pasa) si está `degraded`.
    """
    skill_name = context.get("skill_name")
    if not skill_name:
        return GuardResult(passed=True, guard_name="check_skill_health")

    ls = context.get("living_state")
    if ls is None:
        return GuardResult(passed=True, guard_name="check_skill_health")

    try:
        health = ls.get_skill_health(skill_name)
    except Exception:
        return GuardResult(passed=True, guard_name="check_skill_health")

    status = health.get("status", "unknown")
    fails = health.get("consecutive_failures", 0)

    if status == "broken":
        return GuardResult(
            passed=False,
            reason=(
                f"Skill '{skill_name}' está rota "
                f"({fails} fallos consecutivos). "
                f"No se puede ejecutar en este estado."
            ),
            guard_name="check_skill_health",
            suggested_fix=f"Usar /diagnose para diagnosticar la skill '{skill_name}'",
        )
    if status == "degraded":
        return GuardResult(
            passed=True,
            guard_name="check_skill_health",
            reason=f"Skill '{skill_name}' degradada ({fails} fallos)",
            enrichment={"skill_health_warning": f"{skill_name} degraded ({fails} fallos)"},
        )
    return GuardResult(passed=True, guard_name="check_skill_health")


def verify_dependencies(context: dict) -> GuardResult:
    """Placeholder — siempre pasa. Reservado para verificar deps dinámicas."""
    return GuardResult(passed=True, guard_name="verify_dependencies")


def check_playwright_installed(context: dict) -> GuardResult:
    """Verifica que playwright o npx estén disponibles en PATH."""
    if shutil.which("playwright") or shutil.which("npx"):
        return GuardResult(passed=True, guard_name="check_playwright_installed")
    return GuardResult(
        passed=False,
        reason="Playwright no está instalado o no está en PATH",
        guard_name="check_playwright_installed",
        suggested_fix="pip install playwright && playwright install",
    )


def verify_browser_binaries(context: dict) -> GuardResult:
    """Verifica que al menos un navegador esté disponible en PATH."""
    for browser in ("firefox", "chromium", "chromium-browser", "google-chrome", "chrome"):
        path = shutil.which(browser)
        if path:
            return GuardResult(
                passed=True,
                guard_name="verify_browser_binaries",
                enrichment={"available_browser": browser},
            )
    return GuardResult(
        passed=False,
        reason="No se encontró ningún navegador (firefox/chromium/chrome) en PATH",
        guard_name="verify_browser_binaries",
        suggested_fix="sudo pacman -S firefox  # o instalar el navegador preferido",
    )


def check_token_budget(context: dict) -> GuardResult:
    """Placeholder — siempre pasa. Reservado para integración con token monitor."""
    return GuardResult(passed=True, guard_name="check_token_budget")


def verify_api_availability(context: dict) -> GuardResult:
    """
    Verifica que al menos un LLM esté disponible (cloud API o Ollama local).
    Bloquea si no hay ninguno accesible.
    """
    cloud_keys = [
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
    ]
    if any(os.environ.get(k, "").strip() for k in cloud_keys):
        return GuardResult(passed=True, guard_name="verify_api_availability")
    if shutil.which("ollama"):
        return GuardResult(passed=True, guard_name="verify_api_availability")
    return GuardResult(
        passed=False,
        reason="No hay ningún LLM disponible (ni cloud API ni Ollama)",
        guard_name="verify_api_availability",
        suggested_fix=(
            "Configurar una API key en el .env "
            "(ANTHROPIC_API_KEY / GOOGLE_API_KEY / OPENAI_API_KEY) "
            "o iniciar Ollama con: ollama serve"
        ),
    )


# ── Registro y tabla de precondiciones ────────────────────────────────────────

GUARD_REGISTRY: dict[str, Callable[[dict], GuardResult]] = {
    "verify_current_date":       verify_current_date,
    "check_source_recency":      check_source_recency,
    "check_skill_health":        check_skill_health,
    "verify_dependencies":       verify_dependencies,
    "check_playwright_installed": check_playwright_installed,
    "verify_browser_binaries":   verify_browser_binaries,
    "check_token_budget":        check_token_budget,
    "verify_api_availability":   verify_api_availability,
}

PRECONDITIONS: dict[str, list[str]] = {
    "temporal_search": ["verify_current_date", "check_source_recency"],
    "web_search":      ["verify_current_date", "check_source_recency"],
    "skill_execution": ["check_skill_health"],
    "browser_action":  ["check_playwright_installed", "verify_browser_binaries"],
    "automation":      ["check_playwright_installed", "verify_browser_binaries"],
    "llm_call":        ["verify_api_availability"],
}


def run_guards(action_type: str, context: dict | None = None) -> list[GuardResult]:
    """
    Ejecuta todos los guards registrados para un action_type.
    Las excepciones por guard son capturadas individualmente — un guard roto
    no impide que el resto se ejecute.

    Args:
        action_type: clave en PRECONDITIONS (p.ej. "temporal_search")
        context: datos disponibles para los guards (user_input, living_state, skill_name, etc.)

    Returns:
        Lista de GuardResult en el orden de evaluación.
    """
    ctx = context or {}
    guard_names = PRECONDITIONS.get(action_type, [])
    results: list[GuardResult] = []
    for name in guard_names:
        fn = GUARD_REGISTRY.get(name)
        if fn is None:
            continue
        try:
            r = fn(ctx)
            if not r.guard_name:
                r.guard_name = name
            results.append(r)
        except Exception as exc:
            results.append(GuardResult(
                passed=False,
                reason=f"Guard '{name}' lanzó excepción: {exc}",
                guard_name=name,
            ))
    return results
