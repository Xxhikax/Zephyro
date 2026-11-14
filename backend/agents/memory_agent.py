"""MemoryAgent — knowledge_graph, reminders, schedule."""
from __future__ import annotations

import time
from typing import Optional

from agents.base import AgentResult, BaseAgent
from core.request_context import RequestContext


class MemoryAgent(BaseAgent):
    name = "memory"
    description = "Knowledge graph, recordatorios, agenda, perfil de usuario"
    tools = ["knowledge-graph", "reminders", "schedule"]

    def __init__(self, skill_registry) -> None:
        self._registry = skill_registry

    async def handle(self, task: str, ctx: RequestContext) -> AgentResult:
        t0 = time.perf_counter()
        skill_name = self._pick_skill(task)
        if skill_name is None:
            return AgentResult(
                success=False,
                response="",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        skill = self._registry.get_skill(skill_name)
        if skill is None or not skill.enabled:
            return AgentResult(
                success=False,
                response=f"Skill '{skill_name}' no disponible",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        try:
            response = await skill.handle(task, ctx)
            return AgentResult(
                success=bool(response),
                response=str(response) if response else "",
                skill_used=skill_name,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            return AgentResult(
                success=False,
                response=f"Error en {skill_name}: {exc}",
                skill_used=skill_name,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

    def _pick_skill(self, task: str) -> Optional[str]:
        lower = task.lower()
        if any(k in lower for k in ("agenda", "evento", "clase", "examen", "/hoy", "/agenda", "qué tengo", "que tengo")):
            return "schedule"
        if any(k in lower for k in ("recuérda", "recuerda", "recordá", "recorda", "recuérdame", "recordatorio", "reminder", "/recordar", "/reminders")):
            return "reminders"
        if any(k in lower for k in ("/kg", "knowledge graph", "grafo", "relación entre", "relacion entre")):
            return "knowledge-graph"
        return None
