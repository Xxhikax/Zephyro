"""
BrowserAgent — automatización de browser + web search.

NOTA: La automatización de browser en Zephyro NO está expuesta como skill
del SkillRegistry; vive en modules/automation/browser_controller.py y se
invoca desde el router CAPA 1 / 1.5 del orchestrator. Este agente delega
al orchestrator base para esos casos y solo dispatch directo cuando hay
una skill clara (ej: web_search).
"""
from __future__ import annotations

import time
from typing import Optional

from agents.base import AgentResult, BaseAgent
from core.request_context import RequestContext


_BROWSER_HINTS = (
    "abre", "abrí", "navegador", "browser", "firefox", "chrome",
    "navega a", "ir a", "google", "youtube", "wikipedia",
)
_SEARCH_HINTS = (
    "busca", "búscame", "search", "investigar", "buscar en",
)


class BrowserAgent(BaseAgent):
    name = "browser"
    description = "Browser automation (Playwright via orchestrator) + web search"
    tools = ["web-search"]  # browser automation va por el orchestrator base

    def __init__(self, skill_registry) -> None:
        self._registry = skill_registry

    async def handle(self, task: str, ctx: RequestContext) -> AgentResult:
        t0 = time.perf_counter()
        lower = task.lower()

        # Caso A: si parece automatización del navegador, NO interceptamos —
        # el orchestrator base ya tiene CAPA 1/1.5 con regex + LLM fallback.
        if any(hint in lower for hint in _BROWSER_HINTS):
            return AgentResult(
                success=False,
                response="",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={"reason": "delegate_to_base_orchestrator"},
            )

        # Caso B: búsqueda web — buscar skill que pueda manejarlo
        if any(hint in lower for hint in _SEARCH_HINTS):
            for candidate_name in ("web-search", "web_search", "search"):
                skill = self._registry.get_skill(candidate_name)
                if skill and skill.enabled:
                    try:
                        resp = await skill.handle(task, ctx)
                        return AgentResult(
                            success=bool(resp),
                            response=str(resp) if resp else "",
                            skill_used=candidate_name,
                            elapsed_ms=(time.perf_counter() - t0) * 1000,
                        )
                    except Exception as exc:
                        return AgentResult(
                            success=False,
                            response=f"Error en {candidate_name}: {exc}",
                            skill_used=candidate_name,
                            elapsed_ms=(time.perf_counter() - t0) * 1000,
                        )

        return AgentResult(
            success=False,
            response="",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
