"""
BaseSkill — Clase base abstracta para todas las habilidades de Zephyro.

Cada skill es un directorio con:
  - SKILL.md (frontmatter YAML + body Markdown)
  - skill.py (clase que hereda BaseSkill)

El frontmatter define metadatos (nombre, triggers, requires).
El body Markdown es el contexto completo, cargado on-demand.
"""
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.request_context import RequestContext
# normalize_voseo vive en la fuente única core/verbs.py. Se re-exporta acá para
# compatibilidad con los imports existentes (`from skills.base import normalize_voseo`).
from core.verbs import normalize_voseo  # noqa: F401


@dataclass
class SkillMetadata:
    """Metadatos parseados del frontmatter YAML de SKILL.md."""
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    requires: dict[str, list[str]] = field(default_factory=dict)
    # requires: {python: [networkx], env: [API_KEY], bins: [ffmpeg]}
    provides: dict[str, Any] = field(default_factory=dict)
    # provides: {slash_commands: [{name: "/kg", description: "..."}]}
    enabled: bool = True

    # ── Cruce A↔B: contratos de skill ─────────────────────────────────
    # uses_tools: declaracion explicita de las herramientas/sub-skills que
    # el skill invoca. Util para mostrar grafo en aprobacion de drafts.
    # Ejemplos: ["terminal:read", "terminal:write", "github:read",
    #            "filesystem:read:logs", "introspection:tier2"]
    uses_tools: list[str] = field(default_factory=list)

    # risk_level: "low" | "medium" | "high" — derivado del set uses_tools
    # o explicito en el frontmatter. Skills auto-generadas con risk "high"
    # requieren confirmacion adicional.
    risk_level: str = "low"

    # auto_generated: True si el skill fue propuesto por skill_proposer
    # (Parte B). Permite filtrar/destacar drafts vs skills builtin.
    auto_generated: bool = False

    # source_envelope_ids: si auto_generated, lista de request_ids que la
    # generaron. Permite trazar el origen.
    source_envelope_ids: list[str] = field(default_factory=list)


class BaseSkill(ABC):
    """Protocolo que toda skill debe implementar."""

    def __init__(self, metadata: SkillMetadata, skill_dir: Path):
        self.metadata = metadata
        self.skill_dir = skill_dir
        self._full_context: Optional[str] = None  # lazy-loaded
        self.llm_manager: Optional[Any] = None

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def enabled(self) -> bool:
        return self.metadata.enabled

    @enabled.setter
    def enabled(self, value: bool):
        self.metadata.enabled = value

    # ── Core ──────────────────────────────────────────────────────────

    @abstractmethod
    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        """
        Procesa el input del usuario si esta skill lo maneja.
        Retorna string de respuesta si lo manejó, None si no.
        """
        ...

    async def handle_forced(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        """Dispatch forzado: el classifier cloud (Fase 2) decidió que esta skill
        maneja el input aunque ``matches_command()`` heurístico falle (forma
        rara, fuera de los triggers/patrones).

        Default: reintenta ``handle()`` igual — sirve para skills cuyo guard de
        entrada sea laxo. Las skills con guard duro + extractor propio (p.ej.
        ScheduleSkill, que arranca con ``if not matches_command: return None``
        pero tiene ``_extract_schedule_intent``) DEBEN overridear esto para
        saltear el guard y entrar directo a su extractor LLM."""
        return await self.handle(user_input, ctx)

    def matches_command(self, user_input: str) -> bool:
        """Verifica si el input matchea algún trigger de esta skill.
        Normaliza voseo→tuteo y Unicode NFC para reconocer "agendá"/"poné" etc."""
        lower = normalize_voseo(user_input.lower().strip())  # ya devuelve NFC
        for trigger in self.metadata.triggers:
            # rstrip: varios SKILL.md declaran el trigger con espacio final
            # ("lee el archivo ", "recuérdame "); sin esto, `t + " "` produce
            # doble espacio y el trigger nunca matchea (bug histórico).
            t = unicodedata.normalize("NFC", trigger.lower()).rstrip()
            if lower == t or lower.startswith(t + " ") or lower.startswith(t + "\t"):
                return True
        return False

    def match_confidence(self, user_input: str) -> float:
        """
        Confianza 0..1 de que esta skill maneje el input.

        Default derivado de ``matches_command``: trigger exacto → 1.0, sin
        match → 0.0. Las skills con matching rico (ej. ScheduleSkill, que
        reconoce patrones semánticos además de prefijos) DEBEN overridear
        esto para devolver granularidad — p.ej. 1.0 trigger exacto, 0.6 match
        semántico, 0.4 hint difuso. El ClarificationGate usa este score para
        decidir si ejecutar directo, preguntar, o ignorar.

        Mantener consistencia: si ``match_confidence`` > 0, ``matches_command``
        no necesita ser True (el override puede reconocer más que los triggers),
        pero ``try_handle`` sigue usando ``matches_command`` para el atajo de
        alta confianza. Las skills que amplíen el matching deben asegurarse de
        que ``handle()`` sepa procesar esos inputs extra.
        """
        return 1.0 if self.matches_command(user_input) else 0.0

    # ── Progressive Disclosure ────────────────────────────────────────

    def get_prompt_summary(self) -> str:
        """Una línea para inyectar en el prompt del LLM (nombre + descripción)."""
        cmds = ""
        slash = self.metadata.provides.get("slash_commands", [])
        if slash:
            names = [c["name"] for c in slash if "name" in c]
            if names:
                cmds = f" (comandos: {', '.join(names)})"
        return f"- {self.metadata.name}: {self.metadata.description}{cmds}"

    def get_full_context(self) -> str:
        """Body completo del SKILL.md — cargado on-demand."""
        if self._full_context is None:
            skill_md = self.skill_dir / "SKILL.md"
            if skill_md.exists():
                raw = skill_md.read_text(encoding="utf-8")
                # Strip YAML frontmatter (between --- delimiters)
                parts = raw.split("---", 2)
                self._full_context = parts[2].strip() if len(parts) >= 3 else raw
            else:
                self._full_context = ""
        return self._full_context

    # ── Requirements ──────────────────────────────────────────────────

    def check_requirements(self) -> tuple[bool, list[str]]:
        """
        Verifica que las dependencias estén disponibles.
        Retorna (ok, lista_de_faltantes).
        """
        missing: list[str] = []
        reqs = self.metadata.requires

        # Python packages
        for pkg in reqs.get("python", []):
            try:
                __import__(pkg)
            except ImportError:
                missing.append(f"python:{pkg}")

        # Environment variables
        import os
        for var in reqs.get("env", []):
            if not os.getenv(var):
                missing.append(f"env:{var}")

        # Binaries
        import shutil
        for binary in reqs.get("bins", []):
            if not shutil.which(binary):
                missing.append(f"bin:{binary}")

        return (len(missing) == 0, missing)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def on_load(self) -> None:
        """Hook opcional al cargar la skill. Override para inicialización."""
        pass

    async def on_unload(self) -> None:
        """Hook opcional al descargar la skill."""
        pass

    def set_llm_manager(self, llm: Any) -> None:
        """Inyecta el LLM manager para skills que necesiten diagnóstico o generación."""
        self.llm_manager = llm
