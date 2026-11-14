"""Introspection Skill — análisis de código en tiers (GitNexus → OpenCode → Claude CLI)."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional

from core.request_context import RequestContext
from modules.code_analysis import binary_finder
from modules.code_analysis.opencode_bridge import get_opencode_bridge
from modules.automation.terminal_controller import get_terminal_controller
from skills.base import BaseSkill

log = logging.getLogger("zephyro.introspection")


_DEEP_TRIGGERS = ("con claude", "deep", "profundo en serio", "muy profundo")


def _tier2_model() -> str:
    """Lee el modelo asignado a `introspection_t2` desde provider_config."""
    try:
        from core.provider_config import get_provider_config
        cfg = get_provider_config()
        assignment = cfg.get_task("introspection_t2")
        if assignment.get("model"):
            return assignment["model"]
    except Exception:
        pass
    return os.getenv("OPENCODE_DEFAULT_MODEL", "deepseek/deepseek-v4-pro")


class IntrospectionSkill(BaseSkill):

    def matches_command(self, user_input: str) -> bool:
        if super().matches_command(user_input):
            return True
        lower = user_input.lower().strip()
        return any(p in lower for p in (
            "analiza el repo", "analiza el codigo", "analiza el código",
            "analiza la arquitectura", "explica el codigo", "explica el código",
            "analisis profundo", "análisis profundo",
        ))

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        text = user_input.strip()
        question, repo_path, want_claude = self._parse_input(text)

        if not question:
            return (
                "Decime qué querés analizar. Ej: `/analiza cómo funciona el orchestrator`"
            )

        # Tier 1: GitNexus structural (si está disponible)
        tier1_text = await self._gitnexus_query(question)

        # Tier 4 (Claude CLI) si lo pidió explícitamente
        if want_claude:
            tier4_text = await self._claude_cli(question, repo_path, ctx)
            return self._compose([
                ("Tier 1 — GitNexus", tier1_text),
                ("Tier 4 — Claude Code CLI", tier4_text),
            ])

        # Tier 2 (OpenCode + DeepSeek) por default
        tier2_text = await self._opencode(question, repo_path)
        return self._compose([
            ("Tier 1 — GitNexus", tier1_text),
            (f"Tier 2 — OpenCode ({_tier2_model()})", tier2_text),
        ])

    # ── Parsing ──────────────────────────────────────────────────────

    def _parse_input(self, text: str) -> tuple[str, Optional[Path], bool]:
        lower = text.lower()
        want_claude = any(t in lower for t in _DEEP_TRIGGERS)

        # Strip prefix triggers
        cleaned = text
        for prefix in (
            "/analiza", "/introspect", "/analyze",
            "analiza el repo", "analiza el codigo", "analiza el código",
            "analiza la arquitectura", "explica el codigo", "explica el código",
            "analisis profundo", "análisis profundo", "analiza",
        ):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                break

        # Strip "con claude" del cuerpo
        for t in _DEEP_TRIGGERS:
            cleaned = re.sub(rf"\b{re.escape(t)}\b", "", cleaned, flags=re.IGNORECASE).strip()

        # Detectar repo: "fastapi <pregunta>" → busca en D:/zephyro_repos/fastapi
        repo_path: Optional[Path] = None
        repos_root = Path(os.getenv("ZEPHYRO_REPOS_ROOT", "D:/zephyro_repos"))
        m = re.match(r"^([\w\-\.]+)\s+(.+)$", cleaned)
        if m and (repos_root / m.group(1)).exists():
            repo_path = repos_root / m.group(1)
            cleaned = m.group(2).strip()

        # Si no, usar cwd como default
        if repo_path is None:
            repo_path = Path.cwd()

        return cleaned.strip("?¿:"), repo_path, want_claude

    # ── Tier 1: GitNexus ─────────────────────────────────────────────

    async def _gitnexus_query(self, question: str) -> str:
        try:
            from modules.automation.gitnexus_bridge import GitNexusBridge
        except ImportError:
            return "_(GitNexus no disponible)_"

        bridge = GitNexusBridge()
        started = await bridge.start()
        if not started:
            return "_(GitNexus no inicializado — necesita `npx gitnexus`)_"
        try:
            result = await bridge.search(query=question)
            if not result:
                return "_(sin resultados estructurales)_"
            return bridge.format_for_prompt("search", result, max_tokens=400)
        except Exception as exc:
            log.warning("[introspection] gitnexus error: %s", exc)
            return f"_(GitNexus error: {exc})_"
        finally:
            try:
                await bridge.stop()
            except Exception:
                pass

    # ── Tier 2: OpenCode + DeepSeek ──────────────────────────────────

    async def _opencode(self, question: str, repo_path: Path) -> str:
        bridge = get_opencode_bridge()
        if not await bridge.is_available():
            return (
                "_(OpenCode CLI no instalado. Instalalo con `npm i -g opencode-ai` "
                "y configurá DeepSeek)_"
            )
        result = await bridge.query(
            question, repo_path=repo_path, model=_tier2_model(),
        )
        if not result.ok:
            return f"_(OpenCode error: {result.error})_"
        return result.text or "_(sin respuesta)_"

    # ── Tier 4: Claude Code CLI ──────────────────────────────────────

    async def _claude_cli(
        self, question: str, repo_path: Path, ctx: RequestContext
    ) -> str:
        binary = binary_finder.find("claude")
        if binary is None:
            return "_(Claude Code CLI no instalado)_"

        ctrl = get_terminal_controller()
        if ctrl is None:
            return "_(TerminalController no disponible)_"

        # Quote para PowerShell
        safe_q = question.replace('"', '\\"')
        cmd = f'& "{binary}" -p "{safe_q}" --output-format json'
        result = await ctrl.run(cmd, ctx, cwd=repo_path, timeout=300.0)
        if not result.ok:
            return f"_(Claude CLI error: {result.error or result.stderr})_"
        # Claude Code devuelve JSON estructurado en --output-format json
        return result.stdout.strip()[:5000] or "_(sin respuesta)_"

    # ── Composición ──────────────────────────────────────────────────

    @staticmethod
    def _compose(sections: list[tuple[str, str]]) -> str:
        parts = []
        for title, body in sections:
            if not body:
                continue
            parts.append(f"### {title}\n\n{body}")
        return "\n\n---\n\n".join(parts) if parts else "_(sin resultados)_"
