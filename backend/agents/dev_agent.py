"""DevAgent — git, github, introspection, terminal, file-ops, log-reader."""
from __future__ import annotations

import time
from typing import Optional

from agents.base import AgentResult, BaseAgent
from core.request_context import RequestContext


class DevAgent(BaseAgent):
    name = "dev"
    description = "Operaciones de desarrollo: git, github, código, archivos, logs, terminal"
    tools = ["git-ops", "github", "introspection", "terminal", "file-ops", "log-reader"]

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
        if any(k in lower for k in ("commit", "git push", "git log", "branch", "rebase", "git status", "git diff")):
            return "git-ops"
        if any(k in lower for k in ("pull request", "pr ", "issue ", "github")):
            return "github"
        if any(k in lower for k in ("ejecuta", "corre el comando", "powershell:", "pwsh:", "/run ", "/exec ")):
            return "terminal"
        if any(k in lower for k in ("lee el archivo", "crea archivo", "borra archivo", "/cat ", "/ls ", "/write ", "file-ops")):
            return "file-ops"
        if any(k in lower for k in ("explica el código", "explica la función", "analiza función", "introspect", "cómo funciona", "como funciona")):
            return "introspection"
        if any(k in lower for k in ("log", "error reciente", "errores recientes", "stacktrace")):
            return "log-reader"
        return None
