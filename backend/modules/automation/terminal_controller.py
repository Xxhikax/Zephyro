"""
TerminalController — Ejecutor seguro de comandos PowerShell.

Controlador único (singleton) que invoca PowerShell vía subprocess sin shell=True.
Aplica el `command_classifier` para decidir tier (READ/WRITE/DESTRUCTIVE) y, si
hace falta, pide aprobación al usuario via WebSocketApprovalGate antes de ejecutar.

Diseño:
    - asyncio.create_subprocess_exec(["powershell.exe", "-NoProfile",
      "-NonInteractive", "-Command", cmd])
    - Captura stdout/stderr con cap (default 100KB)
    - Timeout configurable (default 60s)
    - Cada llamada se loguea con request_id en logs/terminal.log
    - Mantiene WeakSet de procesos vivos para limpieza al shutdown
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import weakref
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from core.command_classifier import CommandTier, classify, explain
from core.platform import IS_WINDOWS
from core.request_context import RequestContext

log = logging.getLogger("zephyro.terminal")


_OUTPUT_CAP_BYTES = 100 * 1024  # 100 KB
_DEFAULT_TIMEOUT = 60.0
_LARGE_TIMEOUT = 600.0  # comandos explícitamente largos (clones, builds)

# Para subprocess capturado en Windows: NO usamos DETACHED_PROCESS porque
# desconecta stdout/stderr. Solo CREATE_NO_WINDOW para no flashear consola.
_CREATE_NO_WINDOW = 0x08000000

_API_KEY_REDACT = re.compile(
    r"((?:api[_-]?key|token|secret|password|bearer)[\s=:]+)([\w\-\.]+)",
    re.IGNORECASE,
)


@dataclass
class TerminalResult:
    cmd: str
    tier: str  # "read" | "write" | "destructive"
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    approved: bool  # True si pasó por approval gate o no requería
    truncated: bool = False
    error: Optional[str] = None  # mensaje si bloqueado / timeout / no-binario

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None


class TerminalController:
    """
    Singleton ejecutor de comandos shell. Inyectado en skills via
    web_app.py lifespan (`set_terminal_controller(...)`).
    """

    def __init__(
        self,
        approval_gate: Any | None = None,
        connection_registry: Any | None = None,
    ) -> None:
        self._approval_gate = approval_gate
        self._registry = connection_registry
        self._live_procs: weakref.WeakSet[asyncio.subprocess.Process] = weakref.WeakSet()
        self._lock = asyncio.Lock()

        self._log_path = Path("logs/terminal.log")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── API pública ──────────────────────────────────────────────────

    async def run(
        self,
        cmd: str,
        ctx: RequestContext,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        cwd: Optional[Path] = None,
        allow_destructive: bool = False,
    ) -> TerminalResult:
        """
        Ejecuta un comando PowerShell.

        - READ:        ejecuta sin aprobación
        - WRITE:       requiere approval_gate; si falta gate → bloquea
        - DESTRUCTIVE: bloqueado salvo allow_destructive=True Y aprobación
        """
        start = time.monotonic()
        tier_enum = classify(cmd)
        tier = tier_enum.value
        request_id = ctx.request_id or "no-rid"

        # Bloqueo de destructive sin flag explícito
        if tier_enum == CommandTier.DESTRUCTIVE and not allow_destructive:
            self._audit(request_id, tier, cmd, "BLOCKED_DESTRUCTIVE", -1, 0, "")
            return TerminalResult(
                cmd=_redact(cmd), tier=tier, exit_code=-1,
                stdout="", stderr="", duration_ms=0, approved=False,
                error="Comando destructivo bloqueado. Requiere allow_destructive=True.",
            )

        # Bloqueo desde canales no-Tauri (Telegram/WhatsApp) para WRITE/DESTRUCTIVE
        if tier_enum != CommandTier.READ and ctx.channel_type != "websocket":
            self._audit(request_id, tier, cmd, "BLOCKED_CHANNEL", -1, 0, "")
            return TerminalResult(
                cmd=_redact(cmd), tier=tier, exit_code=-1,
                stdout="", stderr="", duration_ms=0, approved=False,
                error=f"Acción {tier} requiere confirmación desde el cliente principal (Tauri).",
            )

        # Aprobación para WRITE / DESTRUCTIVE
        approved = True
        if tier_enum != CommandTier.READ:
            approved = await self._request_approval(cmd, tier, ctx)
            if not approved:
                self._audit(request_id, tier, cmd, "REJECTED", -1, 0, "")
                return TerminalResult(
                    cmd=_redact(cmd), tier=tier, exit_code=-1,
                    stdout="", stderr="", duration_ms=0, approved=False,
                    error="Acción rechazada por el usuario.",
                )

        # Ejecutar
        try:
            stdout, stderr, exit_code, truncated = await self._exec(
                cmd, timeout=timeout, cwd=cwd
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._audit(request_id, tier, cmd, "TIMEOUT", -1, duration_ms, "")
            return TerminalResult(
                cmd=_redact(cmd), tier=tier, exit_code=-1,
                stdout="", stderr="", duration_ms=duration_ms, approved=approved,
                error=f"Comando excedió timeout de {timeout}s.",
            )
        except FileNotFoundError as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._audit(request_id, tier, cmd, "NO_BINARY", -1, duration_ms, str(e))
            return TerminalResult(
                cmd=_redact(cmd), tier=tier, exit_code=-1,
                stdout="", stderr=str(e), duration_ms=duration_ms, approved=approved,
                error=f"PowerShell no disponible: {e}",
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.exception("[%s] subprocess error: %s", request_id, e)
            self._audit(request_id, tier, cmd, "ERROR", -1, duration_ms, str(e))
            return TerminalResult(
                cmd=_redact(cmd), tier=tier, exit_code=-1,
                stdout="", stderr=str(e), duration_ms=duration_ms, approved=approved,
                error=f"Error de ejecución: {e}",
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        self._audit(request_id, tier, cmd, f"EXIT_{exit_code}", exit_code, duration_ms, "")

        return TerminalResult(
            cmd=_redact(cmd), tier=tier, exit_code=exit_code,
            stdout=stdout, stderr=stderr, duration_ms=duration_ms,
            approved=approved, truncated=truncated,
        )

    async def shutdown(self) -> None:
        """Mata todos los procesos vivos al cierre del backend."""
        for proc in list(self._live_procs):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._live_procs.clear()

    # ── Internos ─────────────────────────────────────────────────────

    async def _exec(
        self,
        cmd: str,
        *,
        timeout: float,
        cwd: Optional[Path],
    ) -> tuple[str, str, int, bool]:
        if not IS_WINDOWS:
            raise RuntimeError("TerminalController solo soporta Windows por ahora.")

        # Forzar UTF-8 en el output para evitar BOM/cp1252 mojibake
        wrapped = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            f"$ProgressPreference='SilentlyContinue'; {cmd}"
        )
        argv = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-OutputFormat", "Text",
            "-Command", wrapped,
        ]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            creationflags=_CREATE_NO_WINDOW,
        )
        self._live_procs.add(proc)

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            raise

        truncated = False
        if len(stdout_b) > _OUTPUT_CAP_BYTES:
            stdout_b = stdout_b[:_OUTPUT_CAP_BYTES]
            truncated = True
        if len(stderr_b) > _OUTPUT_CAP_BYTES:
            stderr_b = stderr_b[:_OUTPUT_CAP_BYTES]
            truncated = True

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return stdout, stderr, proc.returncode or 0, truncated

    async def _request_approval(
        self, cmd: str, tier: str, ctx: RequestContext
    ) -> bool:
        """
        Pide aprobación al usuario. Si no hay gate configurado, bloquea
        por defecto (fail-safe).
        """
        if self._approval_gate is None:
            log.warning(
                "[%s] approval_gate no configurado — bloqueando %s: %s",
                ctx.request_id, tier, cmd[:120],
            )
            return False

        try:
            preview = (
                f"Voy a ejecutar un comando **{tier}**:\n\n```\n{_redact(cmd)}\n```\n\n"
                f"¿Procedo?"
            )
            request_id = ctx.request_id or await self._approval_gate.start(
                str(ctx.user_name or "anon")
            )
            await self._approval_gate.send_approval_request(request_id, preview)
            action = await self._approval_gate.wait_for_approval(request_id, timeout=120.0)
            return action == "view"
        except Exception as e:
            log.exception("[%s] approval flow error: %s", ctx.request_id, e)
            return False

    def _audit(
        self,
        request_id: str,
        tier: str,
        cmd: str,
        status: str,
        exit_code: int,
        duration_ms: int,
        extra: str,
    ) -> None:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            redacted = _redact(cmd).replace("\n", " ")[:300]
            line = (
                f"{ts} [request_id={request_id}] [tier={tier}] "
                f"status={status} exit={exit_code} dur={duration_ms}ms "
                f"cmd={redacted!r} {extra}\n"
            )
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def _redact(cmd: str) -> str:
    """Oculta valores que parecen API keys/tokens en logs."""
    return _API_KEY_REDACT.sub(r"\1<redacted>", cmd)


# ── Singleton accessor ────────────────────────────────────────────────

_controller: Optional[TerminalController] = None


def set_terminal_controller(c: TerminalController) -> None:
    global _controller
    _controller = c


def get_terminal_controller() -> Optional[TerminalController]:
    return _controller
