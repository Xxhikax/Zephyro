"""
Bridge async hacia OpenCode CLI (https://github.com/sst/opencode).

Espejo del patrón de gitnexus_bridge.py pero invocando `opencode run` en
modo headless con `--format json`. Útil para razonamiento sobre código
con modelos baratos (DeepSeek) vía multi-provider.

Uso:
    bridge = OpenCodeBridge()
    if await bridge.is_available():
        result = await bridge.query(
            "Resumí la arquitectura de este repo",
            repo_path=Path("D:/zephyro_repos/fastapi"),
            model="deepseek/deepseek-v4-pro",
        )
        print(result.text)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from modules.code_analysis import binary_finder

log = logging.getLogger("zephyro.opencode")


_CREATE_NO_WINDOW = 0x08000000
_DEFAULT_TIMEOUT = 180.0
_MAX_OUTPUT_BYTES = 500 * 1024  # 500 KB para análisis largos
_API_KEY_REDACT = re.compile(
    r"((?:api[_-]?key|token|secret|password|bearer)[\s=:]+)([\w\-\.]+)",
    re.IGNORECASE,
)


@dataclass
class OpenCodeResult:
    text: str
    session_id: Optional[str]
    model_used: Optional[str]
    duration_ms: int
    exit_code: int
    error: Optional[str] = None
    raw_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None


class OpenCodeBridge:
    """
    Wrapper alrededor del binario `opencode`. No mantiene proceso vivo —
    cada `query()` arranca un subprocess fresh. Mantiene un mapa de
    `session_id` por path de repo para continuidad de contexto.
    """

    def __init__(
        self,
        sessions_path: Optional[Path] = None,
        log_path: Optional[Path] = None,
    ) -> None:
        self._sessions_path = sessions_path or Path("storage/opencode_sessions.json")
        self._sessions_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_path = log_path or Path("logs/opencode.log")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # Lock por repo: opencode no tolera concurrencia segura
        self._locks: dict[str, asyncio.Lock] = {}
        self._opencode_path: Optional[str] = None

    def _resolve_binary(self) -> Optional[str]:
        if self._opencode_path is None:
            self._opencode_path = binary_finder.find("opencode")
        return self._opencode_path

    async def is_available(self) -> bool:
        return self._resolve_binary() is not None

    def invalidate(self) -> None:
        """Tras instalar/desinstalar, borra cache para re-detectar."""
        self._opencode_path = None
        binary_finder.invalidate("opencode")

    # ── Session persistence ──────────────────────────────────────────

    def _load_sessions(self) -> dict[str, str]:
        if not self._sessions_path.exists():
            return {}
        try:
            return json.loads(self._sessions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_sessions(self, sessions: dict[str, str]) -> None:
        try:
            self._sessions_path.write_text(
                json.dumps(sessions, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("[opencode] no pude guardar sessions: %s", exc)

    def session_for(self, repo_path: Path) -> Optional[str]:
        return self._load_sessions().get(str(repo_path.resolve()))

    def reset_session(self, repo_path: Path) -> None:
        sessions = self._load_sessions()
        sessions.pop(str(repo_path.resolve()), None)
        self._save_sessions(sessions)

    # ── Main API ─────────────────────────────────────────────────────

    async def query(
        self,
        prompt: str,
        *,
        repo_path: Optional[Path] = None,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        continue_session: bool = True,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> OpenCodeResult:
        binary = self._resolve_binary()
        if binary is None:
            return OpenCodeResult(
                text="", session_id=None, model_used=model,
                duration_ms=0, exit_code=-1,
                error="opencode CLI no encontrado. Instalá con `npm i -g opencode-ai`.",
            )

        repo_str = str(repo_path.resolve()) if repo_path else "_global_"
        lock = self._locks.setdefault(repo_str, asyncio.Lock())

        # Resolver session_id
        if session_id is None and continue_session and repo_path is not None:
            session_id = self.session_for(repo_path)

        async with lock:
            return await self._run_once(
                binary, prompt, repo_path, model, session_id, timeout, repo_str,
            )

    async def _run_once(
        self,
        binary: str,
        prompt: str,
        repo_path: Optional[Path],
        model: Optional[str],
        session_id: Optional[str],
        timeout: float,
        repo_key: str,
    ) -> OpenCodeResult:
        cmd: list[str] = [
            binary, "run", prompt,
            "--format", "json",
            "-q",  # quiet (sin spinner)
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd += ["-m", model]
        if session_id:
            cmd += ["-s", session_id]

        # Si binary es un .cmd o .ps1, en Windows hay que invocar via shell-equivalent.
        # Pero usamos create_subprocess_exec que respeta extension. Para .cmd usamos cmd.exe wrapper.
        if binary.lower().endswith(".cmd") or binary.lower().endswith(".bat"):
            cmd = ["cmd.exe", "/c"] + cmd

        start = time.monotonic()
        env = dict(os.environ)
        env.setdefault("OPENCODE_DISABLE_TELEMETRY", "1")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(repo_path) if repo_path else None,
                env=env,
                creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError as exc:
            return OpenCodeResult(
                text="", session_id=None, model_used=model,
                duration_ms=0, exit_code=-1,
                error=f"opencode no ejecutable: {exc}",
            )

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
            return OpenCodeResult(
                text="", session_id=session_id, model_used=model,
                duration_ms=int((time.monotonic() - start) * 1000),
                exit_code=-1, error=f"opencode excedió timeout {timeout}s",
            )

        # Truncar
        if len(stdout_b) > _MAX_OUTPUT_BYTES:
            stdout_b = stdout_b[:_MAX_OUTPUT_BYTES]

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        duration_ms = int((time.monotonic() - start) * 1000)

        text, sid, model_used, events = self._parse_events(stdout)
        if sid:
            session_id = sid

        # Persistir session_id
        if session_id and repo_path is not None:
            sessions = self._load_sessions()
            sessions[repo_key] = session_id
            self._save_sessions(sessions)

        self._audit(prompt, model, session_id, proc.returncode or 0, duration_ms)

        if (proc.returncode or 0) != 0:
            return OpenCodeResult(
                text=text, session_id=session_id, model_used=model_used or model,
                duration_ms=duration_ms, exit_code=proc.returncode or 0,
                error=stderr.strip()[:1000] or f"exit {proc.returncode}",
                raw_events=events,
            )

        return OpenCodeResult(
            text=text, session_id=session_id, model_used=model_used or model,
            duration_ms=duration_ms, exit_code=0, raw_events=events,
        )

    @staticmethod
    def _parse_events(stdout: str) -> tuple[str, Optional[str], Optional[str], list[dict]]:
        """
        Parsea stdout JSON-streaming. Cada línea es un evento JSON.
        Extrae:
            - text: concatenación de eventos type=text/content
            - session_id: del último evento que lo contenga
            - model_used: del último evento con model field
        """
        text_parts: list[str] = []
        session_id: Optional[str] = None
        model_used: Optional[str] = None
        events: list[dict] = []

        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                # Output no estructurado, agregar como texto plano
                text_parts.append(line)
                continue
            events.append(evt)
            etype = evt.get("type") or evt.get("event")
            if etype in ("text", "content", "message", "output"):
                payload = evt.get("text") or evt.get("content") or evt.get("message") or ""
                if payload:
                    text_parts.append(str(payload))
            sid = evt.get("session_id") or evt.get("sessionId")
            if sid:
                session_id = sid
            mdl = evt.get("model")
            if mdl:
                model_used = mdl

        # Si no hubo eventos parseables, devolver el stdout crudo
        if not text_parts and stdout.strip():
            text_parts = [stdout]

        return ("\n".join(text_parts).strip(), session_id, model_used, events)

    def _audit(
        self,
        prompt: str,
        model: Optional[str],
        session_id: Optional[str],
        exit_code: int,
        duration_ms: int,
    ) -> None:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            redacted = _API_KEY_REDACT.sub(r"\1<redacted>", prompt[:200])
            line = (
                f"{ts} model={model or '?'} session={session_id or '-'} "
                f"exit={exit_code} dur={duration_ms}ms prompt={redacted!r}\n"
            )
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


# ── Singleton ──────────────────────────────────────────────────────────

_bridge: Optional[OpenCodeBridge] = None


def get_opencode_bridge() -> OpenCodeBridge:
    global _bridge
    if _bridge is None:
        _bridge = OpenCodeBridge()
    return _bridge
