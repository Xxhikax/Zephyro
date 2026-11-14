"""
Consciousness — Pseudo-consciencia proactiva de Zephyro (Fase 3).

Coordina tres sistemas:
  - LivingState (estado vivo, Fase 2)
  - PreconditionGuards (evaluación pre-acción)
  - ConsciousnessSnapshot (contexto comprimido para el LLM)

Interfaz pública:
  consciousness.pre_execute(action_type, user_input, analysis) → ExecutionDecision
  consciousness.post_execute(action_type, result)
  consciousness.get_snapshot(tier=1) → str
  consciousness.introspect() → str

Singleton: get_consciousness() / reset_consciousness() (igual que EventBus).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from core.precondition_guards import run_guards

log = logging.getLogger("zephyro.consciousness")


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass
class ExecutionDecision:
    """Resultado de pre_execute(): permite o bloquea la acción."""
    allowed: bool
    reason: str = ""
    enriched_context: str = ""          # snapshot para inyectar al LLM
    suggested_fix: Optional[str] = None
    auto_resolved: bool = False         # True si algún guard enriqueció sin bloquear
    guard_results: list = field(default_factory=list)


# ── Clase principal ───────────────────────────────────────────────────────────

class Consciousness:
    """
    Orquestador de la pseudo-consciencia proactiva de Zephyro.

    Debe inicializarse con initialize() después de que LivingState esté activo
    (desde el lifespan de web_app.py).
    """

    def __init__(self) -> None:
        self._snapshot: Any = None          # ConsciousnessSnapshot, lazy
        self._ls_getter: Optional[Callable] = None
        self._initialized = False

    def initialize(self, living_state_getter: Callable[[], Optional[Any]]) -> None:
        """
        Inicializa con un getter lazy de LivingState para evitar imports circulares.
        Se llama desde web_app.py lifespan después de que LivingState esté activo.
        """
        from core.consciousness_snapshot import ConsciousnessSnapshot
        self._ls_getter = living_state_getter
        self._snapshot = ConsciousnessSnapshot(living_state_getter)
        self._initialized = True
        log.info("Consciousness inicializado")

    # ── API pública ───────────────────────────────────────────────────────────

    def pre_execute(
        self,
        action_type: str,
        user_input: str = "",
        analysis: dict | None = None,
        skill_name: str | None = None,
    ) -> ExecutionDecision:
        """
        Evalúa precondiciones ANTES de ejecutar una acción.

        Flujo:
          1. Construir contexto con datos disponibles
          2. Ejecutar guards para el action_type
          3. Si algún guard bloquea → retornar allowed=False
          4. Recopilar enriquecimiento (verified_date, etc.) → actualizar snapshot
          5. Generar snapshot tier=1 → incluir en enriched_context

        Args:
            action_type: "temporal_search" | "browser_action" | "llm_call" | etc.
            user_input:  texto del usuario (para check_source_recency)
            analysis:    dict del router (needs_web_search, intent, etc.)
            skill_name:  nombre de la skill (para check_skill_health)
        """
        ctx: dict = {
            "user_input": user_input,
            "skill_name": skill_name,
            "living_state": self._ls_getter() if self._ls_getter else None,
        }
        if analysis:
            ctx.update(analysis)

        results = run_guards(action_type, ctx)

        enrichment: dict[str, Any] = {}
        blocked = None

        for result in results:
            if not result.passed:
                blocked = result
                if self._snapshot:
                    self._snapshot.record_guard_failure(result.guard_name)
                log.warning(
                    "[consciousness] Guard '%s' bloqueó acción '%s': %s",
                    result.guard_name, action_type, result.reason,
                )
                break  # primer guard bloqueante es suficiente
            else:
                if result.enrichment:
                    enrichment.update(result.enrichment)

        # Actualizar fecha verificada en el snapshot
        if "verified_date" in enrichment and self._snapshot:
            self._snapshot.set_verified_date(enrichment["verified_date"])

        # Generar snapshot para LLM
        snapshot = self._snapshot.generate(tier=1) if self._snapshot else self._fallback_snapshot()

        if blocked:
            return ExecutionDecision(
                allowed=False,
                reason=blocked.reason,
                enriched_context=snapshot,
                suggested_fix=blocked.suggested_fix,
                guard_results=results,
            )

        return ExecutionDecision(
            allowed=True,
            reason="Precondiciones pasadas",
            enriched_context=snapshot,
            auto_resolved=bool(enrichment),
            guard_results=results,
        )

    def post_execute(self, action_type: str, result: Any = None) -> None:
        """
        Llamado después de ejecutar una acción.
        Actualmente no hace nada — LivingState se actualiza via SKILL_EXECUTED event.
        Reservado para métricas futuras.
        """
        pass

    def get_snapshot(self, tier: int = 1) -> str:
        """Genera el snapshot actual del estado del sistema."""
        if self._snapshot:
            return self._snapshot.generate(tier=tier)
        return self._fallback_snapshot()

    def introspect(self) -> str:
        """
        Respuesta honesta sobre el estado actual del sistema.
        Para cuando el usuario pregunta '¿por qué fallaste?' o '/introspect'.
        Combina: issues activos + skills rotas + cambios recientes + guard failures hoy.
        """
        lines = ["[Introspección de Zephyro]"]
        _has_problems = False

        ls = self._ls_getter() if self._ls_getter else None

        if ls is not None:
            try:
                # Issues activos
                issues = ls.get_active_issues()
                if issues:
                    _has_problems = True
                    lines.append(f"\nIssues activos ({len(issues)}):")
                    for iss in issues[:5]:
                        lines.append(
                            f"  [{iss.get('module', '?')}] "
                            f"{iss.get('error', '')[:100]} "
                            f"(detectado: {iss.get('detected_at', '?')[:16]})"
                        )

                # Skills con problemas
                skills = ls._state.get("skills", {})
                problem_skills = {
                    k: v for k, v in skills.items()
                    if v.get("status") != "ok"
                }
                if problem_skills:
                    _has_problems = True
                    lines.append(f"\nSkills con problemas ({len(problem_skills)}):")
                    for name, sk in problem_skills.items():
                        rate = sk.get("success_rate")
                        rate_str = f"{rate:.0%}" if rate is not None else "N/A"
                        lines.append(
                            f"  {name}: {sk.get('status', '?')} "
                            f"({sk.get('consecutive_failures', 0)} fallos, "
                            f"success_rate={rate_str})"
                        )

                # Cambios recientes
                changes = ls.get_recent_changes(3)
                if changes:
                    lines.append("\nÚltimos cambios en el codebase:")
                    for c in changes:
                        lines.append(
                            f"  {c.get('commit_hash', '?')[:7]}: "
                            f"{c.get('message', '?')[:60]} "
                            f"({c.get('timestamp', '?')[:10]})"
                        )

                # Info del sistema
                system = ls._state.get("system", {})
                lines.append(
                    f"\nSistema: restart_count={system.get('restart_count', 0)}, "
                    f"uptime_since={system.get('uptime_since', 'N/A')[:16]}"
                )
            except Exception as exc:
                lines.append(f"\n[Error al leer LivingState: {exc}]")

        # Guard failures hoy
        if self._snapshot and self._snapshot._failed_today:
            lines.append("\nGuards fallidos hoy:")
            for guard, count in self._snapshot._failed_today.items():
                lines.append(f"  {guard}: {count}x")

        if not _has_problems and not (self._snapshot and self._snapshot._failed_today):
            lines.append("\nSin issues activos, skills en buen estado, sin fallos recientes.")

        # Fecha verificada
        lines.append(f"\nFecha verificada del servidor: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(lines)

    # ── Helpers privados ──────────────────────────────────────────────────────

    @staticmethod
    def _fallback_snapshot() -> str:
        """Snapshot mínimo cuando Consciousness no está inicializado."""
        now = datetime.now()
        return (
            f"[SYSTEM STATE - verified {now.strftime('%H:%M:%S')}]\n"
            f"date: {now.strftime('%Y-%m-%d')} (server-verified)"
        )


# ── Singleton global ──────────────────────────────────────────────────────────

_consciousness: Consciousness | None = None


def get_consciousness() -> Consciousness:
    """Retorna el singleton global de Consciousness."""
    global _consciousness
    if _consciousness is None:
        _consciousness = Consciousness()
    return _consciousness


def reset_consciousness() -> None:
    """Resetea el singleton — útil en tests para aislar estado."""
    global _consciousness
    _consciousness = None
