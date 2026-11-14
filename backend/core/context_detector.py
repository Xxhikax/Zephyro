"""
ContextDetector — background async loop que detecta la ventana activa del SO
y emite context:update via WebSocket cuando cambia.

Detecta:
- VSCode: extrae nombre de proyecto del título
- Explorer / nautilus: path absoluto si está en el título
- Navegador (Firefox/Chrome/Edge): consulta BrowserController.page.url
- Otros: solo active_window

Filtra ventanas propias de Zephyro (CLI, overlay Tauri) para evitar bucles.

Conflict resolution: manual override (POST /context/workspace) tiene prioridad
sobre auto durante 10 minutos. Después vuelve a auto.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("zephyro.context_detector")

try:
    import pygetwindow as gw
    _HAS_GW = True
except ImportError:
    _HAS_GW = False

_POLL_INTERVAL = 2.0      # segundos entre lecturas
_MANUAL_LOCK_SECONDS = 600  # 10 minutos
# Patrones para detectar ventanas de Zephyro y filtrarlas. NO usar "zephyro"
# bare como substring — el nombre del proyecto en sí es "zephyro" y aparece
# en títulos de VSCode normales, causaría falsos positivos.
_OWN_PREFIX_PATTERNS = ("zephyro ", "zephyro-")  # "ZEPHYRO Terminal", "Zephyro - x"
_OWN_SUBSTRING_PATTERNS = (
    "jarvis-terminal", "zephyro cli", "tauri", "claude code", "claude-code",
)
_OWN_EXACT_PATTERNS = ("zephyro",)  # título exactamente "ZEPHYRO"

# Regex para títulos VSCode: "<file> - <project> - Visual Studio Code"
# Permite hyphens en project name (lazy match anclado al sufijo).
_VSCODE_RE = re.compile(
    r"^(?P<file>.+?)\s+-\s+(?P<project>.+?)\s+-\s+Visual Studio Code\s*$",
    re.IGNORECASE,
)
# Cursor (fork de VSCode) usa el mismo patrón
_CURSOR_RE = re.compile(
    r"^(?P<file>.+?)\s+-\s+(?P<project>.+?)\s+-\s+Cursor\s*$",
    re.IGNORECASE,
)
_BROWSER_TITLE_HINTS = ("firefox", "chrome", "edge", "chromium", "navegador")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContextDetector:
    """Singleton: corre como tarea background del lifespan."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._manual_until: float = 0.0
        # registry/orchestrator se inyectan via configure()
        self._registry = None
        self._get_browser_url_fn = None
        self._workspace_store = None

        self._context: dict[str, Any] = {
            "active_window": None,
            "active_path": None,
            "active_project": None,
            "current_url": None,
            "vscode_project": None,
            "detected_by": "auto",
            "last_updated": _now_iso(),
        }

    def configure(
        self,
        registry,
        get_browser_url_fn,
        workspace_store=None,
    ) -> None:
        """Inyección de dependencias — llamado desde el lifespan."""
        self._registry = registry
        self._get_browser_url_fn = get_browser_url_fn
        self._workspace_store = workspace_store
        # Recuperar último workspace manual si existe
        if workspace_store is not None:
            try:
                active = workspace_store.get_active()
                if active:
                    self._context["active_project"] = active
                    self._context["detected_by"] = "manual"
                    log.info(f"[context] workspace activo recuperado: {active}")
            except Exception as exc:
                log.debug(f"[context] no se pudo recuperar workspace: {exc}")

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        if not _HAS_GW:
            log.warning("[context] pygetwindow no disponible — detector deshabilitado")
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="context-detector")
        log.info("[context] detector iniciado")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None
        self._stop_event = None
        log.info("[context] detector detenido")

    # ── Public API ─────────────────────────────────────────────────────

    def get_current(self) -> dict[str, Any]:
        """Devuelve copia del contexto actual."""
        return dict(self._context)

    async def set_manual_workspace(self, name: str, path: Optional[str] = None) -> dict[str, Any]:
        """
        Override manual: bloquea auto-detection por _MANUAL_LOCK_SECONDS.
        Persiste en SessionWorkspace si está configurado.
        """
        clean = (name or "").strip()
        if not clean:
            return self._context
        self._context["active_project"] = clean
        if path:
            self._context["active_path"] = path
        self._context["detected_by"] = "manual"
        self._context["last_updated"] = _now_iso()
        self._manual_until = time.monotonic() + _MANUAL_LOCK_SECONDS

        if self._workspace_store is not None:
            try:
                self._workspace_store.set_active(clean)
            except Exception as exc:
                log.debug(f"[context] persist failed: {exc}")

        await self._broadcast()
        return self.get_current()

    # ── Main loop ──────────────────────────────────────────────────────

    async def _run(self) -> None:
        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:
                log.debug(f"[context] tick error: {exc}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=_POLL_INTERVAL)
                return
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        # Si hay manual lock activo no sobrescribir
        if time.monotonic() < self._manual_until:
            return

        title = await asyncio.to_thread(self._get_active_window_title)
        if not title:
            return
        if self._is_own_window(title):
            return

        new_ctx = await self._build_context_from_title(title)
        if not self._changed(new_ctx):
            return

        self._context.update(new_ctx)
        self._context["last_updated"] = _now_iso()
        await self._broadcast()

    # ── Detection helpers ──────────────────────────────────────────────

    @staticmethod
    def _get_active_window_title() -> str:
        """Wrapper sincrono — corre en thread."""
        try:
            win = gw.getActiveWindow()
            if win and getattr(win, "title", None):
                return win.title
        except Exception:
            pass
        return ""

    @staticmethod
    def _is_own_window(title: str) -> bool:
        if not title:
            return False
        t = title.lower().strip()
        if t in _OWN_EXACT_PATTERNS:
            return True
        if any(t.startswith(p) for p in _OWN_PREFIX_PATTERNS):
            return True
        if any(p in t for p in _OWN_SUBSTRING_PATTERNS):
            return True
        return False

    async def _build_context_from_title(self, title: str) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "active_window": title,
            "active_path": self._context.get("active_path"),
            "active_project": self._context.get("active_project"),
            "current_url": None,
            "vscode_project": None,
            "detected_by": "auto",
        }

        # VSCode / Cursor
        for regex in (_VSCODE_RE, _CURSOR_RE):
            m = regex.match(title)
            if m:
                project = m.group("project").strip()
                ctx["active_project"] = project
                ctx["vscode_project"] = project
                return ctx

        # Browser: consultar BrowserController por URL (best-effort, no bloquea)
        title_lower = title.lower()
        if any(h in title_lower for h in _BROWSER_TITLE_HINTS):
            url = await self._fetch_browser_url()
            if url:
                ctx["current_url"] = url
            return ctx

        # Explorer / file manager: si título empieza con letra de unidad
        # (Windows: "C:\Users\..." o nombre de carpeta)
        if len(title) > 3 and title[1:3] == ":\\":
            ctx["active_path"] = title
            return ctx

        return ctx

    async def _fetch_browser_url(self) -> Optional[str]:
        if self._get_browser_url_fn is None:
            return None
        try:
            url = await self._get_browser_url_fn()
            if url and isinstance(url, str):
                return url
        except Exception as exc:
            log.debug(f"[context] fetch_browser_url failed: {exc}")
        return None

    # ── Change detection + broadcast ──────────────────────────────────

    def _changed(self, new_ctx: dict[str, Any]) -> bool:
        for key in ("active_window", "active_project", "active_path", "current_url"):
            if new_ctx.get(key) != self._context.get(key):
                return True
        return False

    async def _broadcast(self) -> None:
        if self._registry is None:
            return
        payload = {"type": "context:update", **self.get_current()}
        try:
            await self._registry.broadcast(payload)
        except Exception as exc:
            log.debug(f"[context] broadcast failed: {exc}")


# Singleton
detector = ContextDetector()
