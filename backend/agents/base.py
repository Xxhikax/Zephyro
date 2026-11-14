"""BaseAgent — ABC y dataclasses compartidos por todos los agentes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentResult:
    success: bool
    response: str
    evidence: str = ""
    skill_used: str = ""
    elapsed_ms: float = 0.0
    verification: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class BaseAgent(ABC):
    """Agente coordinador que delega a skills del SkillRegistry."""

    name: str = ""
    description: str = ""
    tools: list[str] = field(default_factory=list)  # skill names que puede usar

    @abstractmethod
    async def handle(self, task: str, ctx: Any) -> AgentResult:
        """Maneja una tarea y retorna un AgentResult."""
        ...

    async def _dispatch_skill(
        self,
        skill_name: str,
        user_input: str,
        ctx: Any,
    ) -> Optional[str]:
        """Despacha a un skill específico del SkillRegistry."""
        try:
            from skills.registry import SkillRegistry
            from web_app import app as _app
            registry: SkillRegistry = _app.state.orchestrator.skill_registry
            skill = registry.get_skill(skill_name)
            if skill is None or not skill.enabled:
                return None
            return await skill.handle(user_input, ctx)
        except Exception as exc:
            return f"Error en skill '{skill_name}': {exc}"
