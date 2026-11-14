"""
SkillProposer — destila TaskEnvelopes exitosos y complejos en drafts de
skills nuevas (estilo Hermes self-improving skills).

Flujo:
    1. nudge() corre periodicamente (scheduler cada 30min)
    2. Lee TaskEnvelopes recientes que cumplen filtros
       (success=True, complexity >= 0.6, steps >= 3)
    3. Por cada envelope sin draft existente similar:
       a. Compone un prompt con los pasos del envelope
       b. Llama LLM (provider configurado para "skill_proposer")
       c. Parsea la respuesta como SKILL.md + skill.py
       d. Aplica draft_linter sobre skill.py — si falla, descarta
       e. Guarda en storage/skill_drafts/PENDING/<slug>/
       f. Emite evento SKILL_PROPOSED al frontend para revision

Cap diario: max DAILY_PROPOSAL_CAP drafts por dia para evitar runaway costs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.task_envelope import TaskEnvelope, get_envelope_store, _coerce_float
from skills.draft_linter import lint_draft, LintResult

log = logging.getLogger("zephyro.skill_proposer")


# ── Config ─────────────────────────────────────────────────────────────

MIN_COMPLEXITY = 0.6
MIN_STEPS = 3
DAILY_PROPOSAL_CAP = 10
LOOKBACK_HOURS = 6  # ventana de envelopes a considerar por nudge

DRAFTS_ROOT = Path("storage/skill_drafts")
PENDING_DIR = DRAFTS_ROOT / "PENDING"
APPROVED_LOG = DRAFTS_ROOT / "approved.jsonl"
REJECTED_LOG = DRAFTS_ROOT / "rejected.jsonl"


_PROPOSER_PROMPT = """Sos un sistema que destila workflows reusables en habilidades reutilizables (skills) para Zephyro, un asistente IA con backend Python/FastAPI.

Te paso una tarea que el usuario completó exitosamente. Mira los pasos que se ejecutaron y proponé una skill que automatice este patrón en el futuro.

REGLAS DURAS:
1. La skill debe HEREDAR de BaseSkill (importar de skills.base).
2. NUNCA importar: subprocess, os.system, eval, exec, ctypes.
3. NUNCA escribir su propia ejecución de shell — DEBE usar TerminalSkill via get_terminal_controller().
4. NUNCA tocar el sistema de archivos directamente fuera de zephyro_workspace.
5. uses_tools en el SKILL.md debe declarar TODAS las skills/herramientas que invoca.

DEVOLVÉ JSON con esta estructura exacta:
{
  "name": "<slug-kebab-case>",
  "description": "<una linea>",
  "triggers": ["<frase 1>", "<frase 2>"],
  "uses_tools": ["terminal:read", "github:read", ...],
  "risk_level": "low|medium|high",
  "skill_md_body": "<markdown body completo del SKILL.md>",
  "skill_py": "<codigo Python completo del skill.py>"
}

TAREA EJEMPLO:
{envelope_json}

Devolvé SOLO el JSON, sin texto extra antes ni después.
"""


# ── Dataclass de propuesta ─────────────────────────────────────────────

@dataclass
class SkillDraft:
    name: str
    description: str
    triggers: list[str]
    uses_tools: list[str]
    risk_level: str
    skill_md_body: str
    skill_py: str
    source_envelope_ids: list[str] = field(default_factory=list)
    proposed_at: float = field(default_factory=time.time)
    lint_result: Optional[LintResult] = None

    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9\-]+", "-", self.name.lower()).strip("-")
        return s or f"draft-{int(self.proposed_at)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "uses_tools": self.uses_tools,
            "risk_level": self.risk_level,
            "skill_md_body": self.skill_md_body,
            "skill_py": self.skill_py,
            "source_envelope_ids": self.source_envelope_ids,
            "proposed_at": self.proposed_at,
            "lint_ok": self.lint_result.ok if self.lint_result else None,
            "lint_summary": self.lint_result.summary() if self.lint_result else None,
        }


# ── Proposer ───────────────────────────────────────────────────────────

class SkillProposer:

    def __init__(
        self,
        llm_caller: Optional[Any] = None,
        envelope_store: Optional[Any] = None,
        drafts_root: Optional[Path] = None,
    ) -> None:
        """
        llm_caller: callable async que recibe (prompt: str) y devuelve string.
        envelope_store: TaskEnvelopeStore (default singleton).
        drafts_root: override del directorio de drafts (para tests).
        """
        self._llm_caller = llm_caller
        self._envelope_store = envelope_store or get_envelope_store()
        self._drafts_root = drafts_root or DRAFTS_ROOT
        self._pending_dir = self._drafts_root / "PENDING"
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._approved_log = self._drafts_root / "approved.jsonl"
        self._rejected_log = self._drafts_root / "rejected.jsonl"

    async def nudge(self) -> dict[str, Any]:
        """Run periódico. Devuelve resumen de la ronda."""
        if self._llm_caller is None:
            log.debug("[skill_proposer] sin llm_caller — skip")
            return {"skipped": True, "reason": "no_llm"}

        if self._daily_count() >= DAILY_PROPOSAL_CAP:
            log.info("[skill_proposer] cap diario alcanzado (%d)", DAILY_PROPOSAL_CAP)
            return {"skipped": True, "reason": "daily_cap"}

        since = time.time() - (LOOKBACK_HOURS * 3600)
        envelopes = self._envelope_store.successful_complex(
            min_complexity=MIN_COMPLEXITY, min_steps=MIN_STEPS, limit=20,
        )
        envelopes = [e for e in envelopes if _coerce_float(e.started_at) >= since]
        if not envelopes:
            return {"considered": 0, "proposed": 0}

        proposed_count = 0
        for env in envelopes:
            if self._already_drafted(env):
                continue
            try:
                draft = await self._propose_one(env)
                if draft is not None:
                    self._save_draft(draft)
                    proposed_count += 1
                    if self._daily_count() >= DAILY_PROPOSAL_CAP:
                        break
            except Exception as exc:
                log.exception("[skill_proposer] propose fallo para %s: %s",
                              env.request_id, exc)

        return {"considered": len(envelopes), "proposed": proposed_count}

    # ── Internal ─────────────────────────────────────────────────────

    async def _propose_one(self, envelope: TaskEnvelope) -> Optional[SkillDraft]:
        env_summary = self._envelope_to_prompt_input(envelope)
        prompt = _PROPOSER_PROMPT.replace("{envelope_json}", env_summary)

        raw = await self._llm_caller(prompt)
        data = self._parse_llm_response(raw)
        if data is None:
            log.warning("[skill_proposer] respuesta LLM no parseable")
            return None

        draft = SkillDraft(
            name=data.get("name", "untitled"),
            description=data.get("description", ""),
            triggers=list(data.get("triggers", [])),
            uses_tools=list(data.get("uses_tools", [])),
            risk_level=str(data.get("risk_level", "low")),
            skill_md_body=str(data.get("skill_md_body", "")),
            skill_py=str(data.get("skill_py", "")),
            source_envelope_ids=[envelope.request_id],
        )

        # Linter — si falla, registramos y descartamos
        result = lint_draft(draft.skill_py)
        draft.lint_result = result
        if not result.ok:
            log.info("[skill_proposer] draft '%s' rechazado por linter: %s",
                     draft.name, result.summary())
            self._log_rejection(draft, [str(i) for i in result.errors])
            return None

        return draft

    def _envelope_to_prompt_input(self, env: TaskEnvelope) -> str:
        steps_brief = [
            {
                "skill": s.skill_name, "args": s.args,
                "result": (s.result_summary or "")[:300],
                "duration_ms": s.duration_ms,
            }
            for s in env.steps
        ]
        return json.dumps({
            "user_input": env.user_input,
            "intent": env.intent,
            "steps": steps_brief,
            "complexity": env.complexity_score,
            "duration_ms": env.duration_ms,
            "final_response": (env.final_response or "")[:500],
        }, ensure_ascii=False, indent=2)

    @staticmethod
    def _parse_llm_response(raw: str) -> Optional[dict[str, Any]]:
        text = raw.strip()
        # Strip Markdown code fences si los hay
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Intento: encontrar el primer { y matchear braces balanceados
            start = text.find("{")
            if start < 0:
                return None
            depth = 0
            for i, ch in enumerate(text[start:], start=start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            return None
            return None

    def _save_draft(self, draft: SkillDraft) -> Path:
        slug = draft.slug()
        dir_path = self._pending_dir / slug
        dir_path.mkdir(parents=True, exist_ok=True)

        # SKILL.md con frontmatter
        frontmatter_lines = [
            "---",
            f"name: {draft.name}",
            f"description: \"{draft.description}\"",
            "triggers:",
            *[f"  - \"{t}\"" for t in draft.triggers],
            "uses_tools:",
            *[f"  - \"{t}\"" for t in draft.uses_tools],
            f"risk_level: {draft.risk_level}",
            "auto_generated: true",
            "source_envelope_ids:",
            *[f"  - \"{rid}\"" for rid in draft.source_envelope_ids],
            "enabled: true",
            "---",
            "",
            draft.skill_md_body,
        ]
        (dir_path / "SKILL.md").write_text(
            "\n".join(frontmatter_lines), encoding="utf-8"
        )
        (dir_path / "skill.py").write_text(draft.skill_py, encoding="utf-8")
        # Metadata para la UI
        (dir_path / "_meta.json").write_text(
            json.dumps(draft.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("[skill_proposer] draft guardado: %s", dir_path)

        try:
            from core.connection_registry import registry
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(registry.broadcast({
                    "type": "skill_proposed",
                    "slug": slug,
                    "name": draft.name,
                    "description": draft.description,
                    "risk_level": draft.risk_level,
                }))
        except Exception:
            pass

        return dir_path

    def _already_drafted(self, env: TaskEnvelope) -> bool:
        """Skip si ya hay un draft que referencia este envelope_id."""
        for meta_path in self._pending_dir.glob("*/_meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if env.request_id in meta.get("source_envelope_ids", []):
                    return True
            except (OSError, json.JSONDecodeError):
                continue
        return False

    def _daily_count(self) -> int:
        cutoff = time.time() - 86400
        count = 0
        for meta_path in self._pending_dir.glob("*/_meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("proposed_at", 0) >= cutoff:
                    count += 1
            except (OSError, json.JSONDecodeError):
                continue
        # Sumar también drafts aprobados/rechazados hoy
        for log_file in (self._approved_log, self._rejected_log):
            if not log_file.exists():
                continue
            try:
                for line in log_file.read_text(encoding="utf-8").splitlines():
                    try:
                        entry = json.loads(line)
                        if entry.get("at", 0) >= cutoff:
                            count += 1
                    except json.JSONDecodeError:
                        continue
            except OSError:
                continue
        return count

    def _log_rejection(self, draft: SkillDraft, reasons: list[str]) -> None:
        try:
            entry = {
                "at": time.time(),
                "name": draft.name,
                "reasons": reasons[:10],
                "source_envelope_ids": draft.source_envelope_ids,
            }
            with self._rejected_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ── Approval / rejection desde la UI ─────────────────────────────

    def list_pending(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for meta_path in sorted(self._pending_dir.glob("*/_meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["slug"] = meta_path.parent.name
                out.append(meta)
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def approve(self, slug: str, *, target_dir: Optional[Path] = None) -> dict[str, Any]:
        """
        Mueve el draft de PENDING a `skills/community/<slug>/` para que el
        SkillLoader watchdog lo cargue.
        """
        src = self._pending_dir / slug
        if not src.exists():
            return {"ok": False, "error": "slug no encontrado"}
        target_root = target_dir or Path("skills/community")
        target_root.mkdir(parents=True, exist_ok=True)
        dst = target_root / slug
        if dst.exists():
            return {"ok": False, "error": f"ya existe en community: {dst}"}

        # Mover archivos
        dst.mkdir(parents=True)
        for f in src.iterdir():
            if f.name == "_meta.json":
                continue  # solo PENDING tiene meta
            (dst / f.name).write_bytes(f.read_bytes())

        try:
            entry = {
                "at": time.time(),
                "slug": slug,
                "moved_to": str(dst),
            }
            with self._approved_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

        # Limpiar PENDING
        for f in src.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            src.rmdir()
        except OSError:
            pass

        return {"ok": True, "slug": slug, "moved_to": str(dst)}

    def reject(self, slug: str, *, reason: str = "user_rejected") -> dict[str, Any]:
        src = self._pending_dir / slug
        if not src.exists():
            return {"ok": False, "error": "slug no encontrado"}
        try:
            entry = {"at": time.time(), "slug": slug, "reason": reason}
            with self._rejected_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
        for f in src.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            src.rmdir()
        except OSError:
            pass
        return {"ok": True, "slug": slug}


# ── Singleton ──────────────────────────────────────────────────────────

_proposer: Optional[SkillProposer] = None


def get_skill_proposer() -> SkillProposer:
    global _proposer
    if _proposer is None:
        _proposer = SkillProposer()
    return _proposer


def set_skill_proposer(p: SkillProposer) -> None:
    global _proposer
    _proposer = p
