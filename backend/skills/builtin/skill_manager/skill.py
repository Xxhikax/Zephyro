"""
SkillManagerSkill — Meta-skill para gestionar el registro de habilidades.
Equivalente al ClawHub local de OpenClaw.
"""
from pathlib import Path
from typing import Optional

from skills.base import BaseSkill, SkillMetadata
from core.request_context import RequestContext


class SkillManagerSkill(BaseSkill):
    """Gestiona skills: listar, habilitar, deshabilitar, info."""

    def __init__(self, metadata: SkillMetadata, skill_dir: Path):
        super().__init__(metadata, skill_dir)
        self._registry = None  # Set by loader after registration

    def set_registry(self, registry):
        """Inyecta referencia al registry (llamado post-registro)."""
        self._registry = registry

    def matches_command(self, user_input: str) -> bool:
        lower = user_input.lower().strip()
        return lower.startswith("/skills") or lower.startswith("/habilidades")

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self._registry:
            return "Error: Skill registry no disponible."

        lower = user_input.lower().strip()
        # Normalize /habilidades -> /skills
        if lower.startswith("/habilidades"):
            lower = "/skills" + lower[len("/habilidades"):]

        parts = lower.split(maxsplit=2)
        subcommand = parts[1] if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""

        if subcommand == "info" and arg:
            return self._info(arg)
        elif subcommand == "enable" and arg:
            return self._enable(arg)
        elif subcommand == "disable" and arg:
            return self._disable(arg)
        elif subcommand == "search" and arg:
            return self._search(arg)
        else:
            return self._list_all()

    def _list_all(self) -> str:
        """Lista todas las skills con estado."""
        skills = self._registry.list_all()
        if not skills:
            return "No hay skills cargadas."

        lines = ["**Habilidades de Zephyro**\n"]
        enabled = [s for s in skills if s.metadata.enabled]
        disabled = [s for s in skills if not s.metadata.enabled]

        if enabled:
            lines.append(f"Activas ({len(enabled)}):")
            for s in enabled:
                triggers = ", ".join(s.metadata.triggers[:3]) if s.metadata.triggers else "auto"
                lines.append(f"  {s.metadata.name} — {s.metadata.description} [{triggers}]")

        if disabled:
            lines.append(f"\nDeshabilitadas ({len(disabled)}):")
            for s in disabled:
                lines.append(f"  {s.metadata.name} — {s.metadata.description}")

        lines.append(f"\nTotal: {len(skills)} skills | Usa /skills info <nombre> para detalles")
        return "\n".join(lines)

    def _info(self, name: str) -> str:
        """Muestra SKILL.md completo de una skill."""
        skill = self._registry.get_skill(name)
        if not skill:
            return f"Skill '{name}' no encontrada. Usa /skills para ver disponibles."

        status = "habilitada" if skill.metadata.enabled else "deshabilitada"
        full_ctx = skill.get_full_context()
        triggers = ", ".join(skill.metadata.triggers) if skill.metadata.triggers else "ninguno"

        reqs = skill.metadata.requires
        req_str = "ninguno"
        if reqs:
            parts = []
            for k, v in reqs.items():
                if v:
                    parts.append(f"{k}: {', '.join(v)}")
            req_str = "; ".join(parts) if parts else "ninguno"

        ok, missing = skill.check_requirements()
        req_status = "OK" if ok else f"Faltantes: {', '.join(missing)}"

        return (
            f"**{skill.metadata.name}** ({status})\n"
            f"Triggers: {triggers}\n"
            f"Requires: {req_str} ({req_status})\n\n"
            f"{full_ctx or skill.metadata.description}"
        )

    def _enable(self, name: str) -> str:
        """Habilita una skill."""
        skill = self._registry.get_skill(name)
        if not skill:
            return f"Skill '{name}' no encontrada."
        if skill.metadata.enabled:
            return f"Skill '{name}' ya esta habilitada."
        skill.metadata.enabled = True
        self._registry._rebuild_command_map()
        return f"Skill '{name}' habilitada."

    def _disable(self, name: str) -> str:
        """Deshabilita una skill."""
        skill = self._registry.get_skill(name)
        if not skill:
            return f"Skill '{name}' no encontrada."
        if name == "skill-manager":
            return "No puedes deshabilitar el skill manager."
        if not skill.metadata.enabled:
            return f"Skill '{name}' ya esta deshabilitada."
        skill.metadata.enabled = False
        self._registry._rebuild_command_map()
        return f"Skill '{name}' deshabilitada."

    def _search(self, query: str) -> str:
        """Busca skills por descripcion o capability."""
        query_lower = query.lower()
        results = []
        for skill in self._registry.list_all():
            if (query_lower in skill.metadata.name.lower() or
                    query_lower in skill.metadata.description.lower()):
                status = "activa" if skill.metadata.enabled else "inactiva"
                results.append(f"  {skill.metadata.name} ({status}) — {skill.metadata.description}")

        if not results:
            return f"No se encontraron skills para '{query}'."
        return f"Resultados para '{query}':\n" + "\n".join(results)
