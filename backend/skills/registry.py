"""
SkillRegistry — Catálogo en memoria de todas las skills cargadas.

Responsabilidades:
- Despacho por comando (try_handle)
- Progressive disclosure (get_prompt_summaries)
- Búsqueda por capability
"""
import threading
import time
from dataclasses import dataclass
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill, normalize_voseo


@dataclass
class SkillCandidate:
    """Una skill que podría manejar un input, con su confianza."""
    skill: BaseSkill
    score: float

    @property
    def name(self) -> str:
        return self.skill.name


@dataclass
class DispatchResult:
    """Resultado de un dispatch: qué skill respondió, con qué y cuánto tardó.
    Permite al orchestrator construir un StepRecord para el loop Hermes."""
    result: str
    skill_name: str
    duration_ms: int


class SkillRegistry:
    """Registro central de skills de Zephyro."""

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}
        self._command_map: dict[str, BaseSkill] = {}  # "/kg" → skill
        self._lock = threading.RLock()

    # ── Registration ──────────────────────────────────────────────────

    def register(self, skill: BaseSkill) -> None:
        """Registra (o reemplaza) una skill."""
        with self._lock:
            self._skills[skill.name] = skill
            # Mapear triggers a la skill
            for trigger in skill.metadata.triggers:
                self._command_map[trigger.lower()] = skill

    def unregister(self, name: str) -> None:
        """Elimina una skill del registro."""
        with self._lock:
            skill = self._skills.pop(name, None)
            if skill:
                # Limpiar command map
                triggers_to_remove = [
                    t for t, s in self._command_map.items() if s is skill
                ]
                for t in triggers_to_remove:
                    del self._command_map[t]

    # ── Dispatch ──────────────────────────────────────────────────────

    async def try_handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        """
        Intenta despachar el input a una skill.
        Retorna la respuesta si alguna skill lo manejó, None si ninguna.
        Wrapper backward-compat sobre dispatch() — devuelve solo el string.
        """
        dr = await self.dispatch(user_input, ctx)
        return dr.result if dr is not None else None

    async def dispatch(
        self, user_input: str, ctx: RequestContext
    ) -> Optional["DispatchResult"]:
        """
        Igual que try_handle pero devuelve qué skill respondió y cuánto tardó,
        para que el orchestrator pueda grabar un StepRecord (loop Hermes).
        """
        with self._lock:
            skills = list(self._skills.values())

        # Normalizamos voseo→tuteo en el punto único de despacho, para que TODAS
        # las skills (las que overridean matches_command con lógica propia y las
        # que usan el default) reconozcan "agendá"/"recordame"/"leé". El handle
        # recibe el normalizado para que su parsing vea el verbo en tuteo; el
        # orchestrator preserva el texto original para el historial.
        norm_input = normalize_voseo(user_input)

        for skill in skills:
            if not skill.enabled:
                continue
            if skill.matches_command(norm_input):
                _t0 = time.perf_counter()
                result = await skill.handle(norm_input, ctx)
                _dur_ms = int((time.perf_counter() - _t0) * 1000)
                if result is not None:
                    try:
                        from core.event_bus import get_bus, SKILL_EXECUTED
                        get_bus().publish(SKILL_EXECUTED, {
                            "skill_name": skill.name,
                            "success": True,
                            "input_preview": user_input[:80],
                            "response": result[:400] if result else "",
                        })
                    except Exception:
                        pass
                    return DispatchResult(result=result, skill_name=skill.name,
                                          duration_ms=_dur_ms)
        return None

    # ── Query ─────────────────────────────────────────────────────────

    def get_skill(self, name: str) -> Optional[BaseSkill]:
        """Obtiene una skill por nombre."""
        return self._skills.get(name)

    def get_all(self) -> list[BaseSkill]:
        """Retorna todas las skills registradas."""
        return list(self._skills.values())

    def list_all(self) -> list[BaseSkill]:
        """Alias de get_all() para SkillManagerSkill."""
        return self.get_all()

    def _rebuild_command_map(self) -> None:
        """Reconstruye el command_map (tras enable/disable)."""
        with self._lock:
            self._command_map.clear()
            for skill in self._skills.values():
                if skill.enabled:
                    for trigger in skill.metadata.triggers:
                        self._command_map[trigger.lower()] = skill

    def get_enabled(self) -> list[BaseSkill]:
        """Retorna solo las skills habilitadas."""
        return [s for s in self._skills.values() if s.enabled]

    def find_by_command(self, command: str) -> Optional[BaseSkill]:
        """Busca skill por trigger/comando."""
        return self._command_map.get(command.lower())

    def find_by_capability(self, capability: str) -> list[BaseSkill]:
        """Busca skills que proveen una capacidad específica."""
        cap_lower = capability.lower()
        results = []
        for skill in self._skills.values():
            # Buscar en provides y en description
            desc = skill.description.lower()
            if cap_lower in desc:
                results.append(skill)
                continue
            for cmd_list in skill.metadata.provides.values():
                if isinstance(cmd_list, list):
                    for item in cmd_list:
                        if isinstance(item, dict):
                            if cap_lower in str(item).lower():
                                results.append(skill)
                                break
        return results

    # ── Skill Suggestion ───────────────────────────────────────────────

    def suggest_for_query(self, user_input: str) -> Optional[BaseSkill]:
        """
        Busca skills deshabilitadas que podrían manejar este input.
        Retorna la skill sugerida o None.
        """
        with self._lock:
            skills = list(self._skills.values())

        for skill in skills:
            if skill.enabled:
                continue  # Solo sugerir skills NO activas
            if skill.matches_command(user_input):
                return skill
        return None

    def rank_candidates(
        self, user_input: str, *, min_score: float = 0.01
    ) -> list[SkillCandidate]:
        """
        Rankea las skills habilitadas por su confianza de manejar el input.

        Usa ``BaseSkill.match_confidence`` (que por default deriva de
        ``matches_command``, pero las skills ricas overridean). Devuelve solo
        los candidatos con score >= ``min_score``, ordenados desc. Es la base
        del ClarificationGate y del orquestador multi-tarea: permite detectar
        ambigüedad real (2+ candidatos con score parecido) en vez del bool
        binario de ``try_handle``.
        """
        with self._lock:
            skills = list(self._skills.values())

        norm_input = normalize_voseo(user_input)
        candidates: list[SkillCandidate] = []
        for skill in skills:
            if not skill.enabled:
                continue
            try:
                score = float(skill.match_confidence(norm_input))
            except Exception:
                # Una skill con match_confidence roto no debe tumbar el ranking
                score = 1.0 if skill.matches_command(norm_input) else 0.0
            if score >= min_score:
                candidates.append(SkillCandidate(skill=skill, score=score))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def get_all_skills(self):
        """Retorna todas las skills (alias para update_from_skills)."""
        return list(self._skills.values())

    # ── Progressive Disclosure ────────────────────────────────────────

    def get_prompt_summaries(self) -> str:
        """
        Genera un índice compacto de todas las skills habilitadas
        para inyectar en el prompt del LLM (nombre + descripción).
        """
        lines = []
        for skill in self._skills.values():
            if skill.enabled:
                lines.append(skill.get_prompt_summary())
        return "\n".join(lines) if lines else ""

    # ── Info ──────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def get_status_summary(self) -> str:
        """Resumen del estado de todas las skills."""
        enabled = sum(1 for s in self._skills.values() if s.enabled)
        total = len(self._skills)
        lines = [f"Skills: {enabled}/{total} activas"]
        for skill in sorted(self._skills.values(), key=lambda s: s.name):
            status = "✓" if skill.enabled else "○"
            triggers = ", ".join(skill.metadata.triggers) if skill.metadata.triggers else "sin triggers"
            lines.append(f"  {status} {skill.name}: {skill.description} [{triggers}]")
        return "\n".join(lines)
