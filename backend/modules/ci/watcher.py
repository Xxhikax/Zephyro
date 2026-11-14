"""CIWatcher — detecta corridas de CI fallidas y las convierte en eventos.

Productor de ``EVENT_CI_RUN_FAILED`` para la Fase 1 (ADR-011). Complementa a la
skill `github`: la skill responde cuando Bruno pregunta (`/ci`), esto avisa sin
que pregunte.

**Opt-in**: requiere ``ZEPHYRO_CI_WATCH=1``. Sin eso no corre, porque significa
un subproceso `gh` cada N minutos para siempre, y no todo repo tiene workflows.

**Read-only por construcción**: ejecuta un argv fijo y hardcodeado
(``gh run list --json … --limit N``) con ``create_subprocess_exec``, sin shell y
sin interpolar nada del usuario. No pasa por TerminalController porque no hay
RequestContext en el loop del notifier — y un argv fijo es más seguro que armar
un string de comando. No existe ninguna ruta que dispare o re-dispare CI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Optional

from core.notification_rules import EVENT_CI_RUN_FAILED, NotificationEvent

log = logging.getLogger("zephyro.ci")

DEFAULT_POLL_SECONDS = 900   # 15 min
DEFAULT_LIMIT = 5
_TIMEOUT = 30.0

# (exit_code, stdout, stderr)
RunnerResult = tuple[int, str, str]
Runner = Callable[[list[str]], Awaitable[RunnerResult]]


async def _default_runner(argv: list[str]) -> RunnerResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return (-1, "", "timeout")
    return (
        proc.returncode if proc.returncode is not None else -1,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


class CIWatcher:

    def __init__(
        self,
        gh_path: Optional[str] = None,
        cwd: Optional[str] = None,
        runner: Optional[Runner] = None,
        poll_seconds: Optional[int] = None,
        limit: int = DEFAULT_LIMIT,
    ):
        self._gh_path = gh_path
        self._cwd = cwd
        self._runner = runner or _default_runner
        self._limit = limit
        self._last_poll: float = 0.0
        self.poll_seconds = int(
            poll_seconds
            if poll_seconds is not None
            else os.getenv("ZEPHYRO_CI_POLL_SECONDS", DEFAULT_POLL_SECONDS)
        )

    # ── Disponibilidad ────────────────────────────────────────────────────

    @staticmethod
    def is_opted_in() -> bool:
        return os.getenv("ZEPHYRO_CI_WATCH", "").strip().lower() in ("1", "true", "yes")

    def gh_path(self) -> Optional[str]:
        if self._gh_path is None:
            try:
                from modules.code_analysis import binary_finder
                self._gh_path = binary_finder.find("gh")
            except Exception as exc:
                log.debug("[ci] no pude ubicar gh: %s", exc)
                self._gh_path = None
        return self._gh_path

    def is_enabled(self) -> bool:
        return self.is_opted_in() and bool(self.gh_path())

    # ── Poll ──────────────────────────────────────────────────────────────

    async def poll(self, now: float, force: bool = False) -> list[NotificationEvent]:
        """Devuelve un evento por corrida fallida reciente.

        NO decide si notificar: eso es de las reglas. El dedup real lo hace el
        log del NotificationCenter con la dedup_key por run id, así una misma
        corrida no avisa dos veces ni después de un reinicio.
        """
        if not force:
            if not self.is_enabled():
                return []
            if now - self._last_poll < self.poll_seconds:
                return []
        self._last_poll = now

        gh = self.gh_path()
        if not gh:
            return []

        argv = [
            gh, "run", "list",
            "--limit", str(self._limit),
            "--status", "failure",
            "--json", "databaseId,workflowName,displayTitle,headBranch,conclusion,createdAt,url",
        ]
        try:
            code, stdout, stderr = await self._runner(argv)
        except Exception as exc:
            log.warning("[ci] gh run list falló: %s", exc)
            return []

        if code != 0:
            log.debug("[ci] gh run list exit=%s: %s", code, (stderr or "").strip()[:200])
            return []
        try:
            runs = json.loads(stdout or "[]")
        except json.JSONDecodeError as exc:
            log.warning("[ci] salida de gh no parseable: %s", exc)
            return []
        if not isinstance(runs, list):
            return []

        return [ev for ev in (self._to_event(r) for r in runs) if ev is not None]

    @staticmethod
    def _to_event(run: Any) -> Optional[NotificationEvent]:
        if not isinstance(run, dict):
            return None
        run_id = run.get("databaseId")
        if run_id is None:
            return None
        workflow = run.get("workflowName") or "workflow"
        branch = run.get("headBranch") or "?"
        subject = (run.get("displayTitle") or "").strip()
        url = run.get("url") or ""

        text = (
            f"🔴 <b>CI fallido</b>\n\n"
            f"<b>{workflow}</b> en <code>{branch}</code>\n"
            f"{subject}"
        )
        if url:
            text += f"\n\n{url}"

        return NotificationEvent(
            event_type=EVENT_CI_RUN_FAILED,
            title=f"CI fallido: {workflow}",
            message=f"{branch} — {subject}" if subject else branch,
            severity="warn",
            dedup_key=f"ci:run:{run_id}",
            payload={
                "subtype": "ci_run_failed",
                "run_id": run_id,
                "workflow": workflow,
                "branch": branch,
                "conclusion": run.get("conclusion") or "failure",
                "url": url,
                "icon": "🔴",
                "category_label": "CI",
                "action": "open_ci",
                "telegram_text": text,
            },
        )


_watcher: Optional[CIWatcher] = None


def get_ci_watcher() -> CIWatcher:
    global _watcher
    if _watcher is None:
        _watcher = CIWatcher()
    return _watcher
