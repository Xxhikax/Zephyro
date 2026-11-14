"""
OrchestratorAgent — coordinador que NO reemplaza core/orchestrator.py.

Estrategia (wrap, not rewrite):
  1. Clasifica el task por keywords → agente especializado (Dev/Browser/System/Memory).
  2. Si ningún agente matchea, delega al Orchestrator existente (chat genérico).
  3. Si el agente especializado retorna AgentResult vacío, también delega.

El Orchestrator de 2885 líneas sigue manejando call mode, TTS, fast_cache,
cross-turn buffer, etc. Esta capa solo añade dispatch a agentes formales.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from agents.base import AgentResult, BaseAgent
from core.request_context import RequestContext

log = logging.getLogger("zephyro.agents.orchestrator")


# Keywords por agente → score (mayor = más prioritario)
_DEV_KEYWORDS = (
    "git", "commit", "branch", "merge", "rebase", "diff", "pull request", "pr ",
    "código", "codigo", "función", "funcion", "archivo", "file", "refactor",
    "introspect", "log", "logs", "error", "stacktrace",
)
_BROWSER_KEYWORDS = (
    "abre", "navegador", "browser", "buscar", "search", "youtube", "google",
    "página", "pagina", "url", "web", "wikipedia", "navega",
)
_SYSTEM_KEYWORDS = (
    "ram", "cpu", "disco", "proceso", "process", "monitor", "abre app", "lanza",
    "ejecuta", "terminal", "powershell", "cmd ", "kill", "matar",
)
_MEMORY_KEYWORDS = (
    "recuerda", "recuérda", "recordá", "recorda", "olvida", "memoria", "perfil",
    "reminder", "recordatorio", "agenda", "evento", "qué dijimos",
    "que dijimos", "conversación", "conversacion",
)


def _score(text: str, keywords: tuple[str, ...]) -> int:
    lower = text.lower()
    return sum(1 for kw in keywords if kw in lower)


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"
    description = "Coordinador que delega a agentes especializados o al orchestrator base"
    tools: list[str] = []  # despacha a otros agentes, no a skills directamente

    def __init__(self, orchestrator: Any) -> None:
        """
        Args:
            orchestrator: instancia de core.orchestrator.Orchestrator (la existente).
        """
        self._orch = orchestrator
        self._agents: dict[str, BaseAgent] = {}
        self._init_agents()

    def _init_agents(self) -> None:
        """Instancia los agentes especializados con el SkillRegistry compartido."""
        from agents.browser_agent import BrowserAgent
        from agents.dev_agent import DevAgent
        from agents.memory_agent import MemoryAgent
        from agents.system_agent import SystemAgent

        registry = self._orch.skill_registry
        self._agents = {
            "dev": DevAgent(registry),
            "browser": BrowserAgent(registry),
            "system": SystemAgent(registry),
            "memory": MemoryAgent(registry),
        }

    def get_agents(self) -> dict[str, BaseAgent]:
        return dict(self._agents)

    def get_agent(self, name: str) -> Optional[BaseAgent]:
        return self._agents.get(name)

    # ── Dispatch ───────────────────────────────────────────────────────

    def classify(self, task: str) -> Optional[str]:
        """Retorna nombre del agente más probable o None si no hay match claro."""
        scores = {
            "dev":     _score(task, _DEV_KEYWORDS),
            "browser": _score(task, _BROWSER_KEYWORDS),
            "system":  _score(task, _SYSTEM_KEYWORDS),
            "memory":  _score(task, _MEMORY_KEYWORDS),
        }
        best = max(scores.items(), key=lambda kv: kv[1])
        return best[0] if best[1] > 0 else None

    async def handle(
        self,
        task: str,
        ctx: RequestContext,
        *,
        force_agent: Optional[str] = None,
    ) -> AgentResult:
        """
        Despacha el task:
          - Si force_agent está, va directo a ese agente.
          - Si no, clasifica por keywords; si matchea, prueba ese agente.
          - Si el agente retorna AgentResult(success=False, response=""),
            cae al Orchestrator existente.
          - Si no hay match, va directo al Orchestrator existente.
        """
        t0 = time.perf_counter()
        target = force_agent or self.classify(task)

        if target and target in self._agents:
            result = await self._agents[target].handle(task, ctx)
            if result.success and result.response:
                result.elapsed_ms = (time.perf_counter() - t0) * 1000
                return result
            log.debug("[orchestrator-agent] %s no resolvió, fallback al orch base", target)

        # Fallback: orchestrator existente
        try:
            response = await self._orch.handle_input(task, ctx=ctx)
            return AgentResult(
                success=bool(response),
                response=str(response) if response else "",
                skill_used="orchestrator_base",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            log.warning("[orchestrator-agent] orch base falló: %s", exc)
            return AgentResult(
                success=False,
                response=f"Error: {exc}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
