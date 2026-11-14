"""Terminal Skill — ejecuta PowerShell via TerminalController."""
from __future__ import annotations

import re
from typing import Optional

from core.request_context import RequestContext
from modules.automation.terminal_controller import (
    TerminalResult, get_terminal_controller,
)
from skills.base import BaseSkill


# Triggers que requieren un comando explícito después
_PREFIX_TRIGGERS = (
    "/run ", "/exec ", "/pwsh ",
    "ejecuta el comando ", "ejecuta este comando ",
    "corre el comando ", "corre este comando ",
    "powershell:", "pwsh:", "ps:",
)

# Indicios de que el input es un comando real y no una pregunta conversacional
# (ej: "¿qué es un cmdlet?" no debería disparar la skill)
_CMDLET_RE = re.compile(
    r"\b(?:Get-|Set-|New-|Remove-|Add-|Test-|Write-|Read-|Invoke-|Start-|Stop-|"
    r"git|gh|npm|pip|python|node|yarn|pnpm|uv|cargo|docker|ls|pwd|cd|cat|"
    r"echo|ipconfig|ping|where)\b",
    re.IGNORECASE,
)


class TerminalSkill(BaseSkill):

    def matches_command(self, user_input: str) -> bool:
        if super().matches_command(user_input):
            return True
        lower = user_input.lower().strip()
        for prefix in _PREFIX_TRIGGERS:
            if lower.startswith(prefix):
                # Verificar que después del prefijo haya un cmdlet/binario reconocible
                rest = user_input[len(prefix):].strip()
                if rest and _CMDLET_RE.search(rest):
                    return True
        return False

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        cmd = self._extract_command(user_input)
        if not cmd:
            return (
                "No detecté un comando. Usá `/run <comando>` o "
                "decime 'ejecuta <comando>'."
            )

        controller = get_terminal_controller()
        if controller is None:
            return "TerminalController no inicializado en el backend."

        # Detectar si es comando largo (clones, builds, instalaciones)
        timeout = 60.0
        if re.search(r"\b(?:clone|install|build|test|update|fetch|pull)\b",
                     cmd, re.IGNORECASE):
            timeout = 300.0

        result = await controller.run(cmd, ctx, timeout=timeout)
        return self._format_result(result)

    def _extract_command(self, user_input: str) -> str:
        text = user_input.strip()
        lower = text.lower()
        for prefix in _PREFIX_TRIGGERS:
            if lower.startswith(prefix):
                return text[len(prefix):].strip().strip("`")
        # Trigger sin prefijo (ej. "/run\n<cmd>")
        for trigger in self.metadata.triggers:
            if lower.startswith(trigger.lower()):
                rest = text[len(trigger):].strip()
                if rest:
                    return rest.strip("`")
        return text

    def _format_result(self, r: TerminalResult) -> str:
        if r.error:
            return f"⛔ {r.error}\n\n_(comando: `{r.cmd}`)_"

        head = f"**{r.tier.upper()}** · exit {r.exit_code} · {r.duration_ms}ms"
        if r.truncated:
            head += " · output truncado (>100KB)"

        body_parts = []
        if r.stdout.strip():
            body_parts.append(f"```\n{r.stdout.rstrip()}\n```")
        if r.stderr.strip():
            body_parts.append(f"_stderr:_\n```\n{r.stderr.rstrip()}\n```")
        if not body_parts:
            body_parts.append("_(sin output)_")

        return f"{head}\n\n" + "\n\n".join(body_parts)
