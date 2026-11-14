"""
BrowserLogWatcher — async tail-follow of playwright.log.

When users subscribe (via WS message browser:subscribe), each new line
appended to backend/playwright.log is pushed as a browser:log event.
While no one subscribes, the watcher idles to avoid wasted I/O.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Set

from core.connection_registry import registry

log = logging.getLogger("zephyro.browser_log_watcher")

_DEFAULT_LOG_PATH = "playwright.log"
_POLL_INTERVAL = 0.5      # segundos cuando hay subscribers
_IDLE_INTERVAL = 5.0      # segundos cuando no hay subscribers


class BrowserLogWatcher:
    """Singleton tail-follow del archivo playwright.log."""

    def __init__(self, log_path: str = _DEFAULT_LOG_PATH):
        self._log_path = Path(log_path)
        self._subscribers: Set[str] = set()
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._wake_event: asyncio.Event | None = None
        self._position: int = 0

    def subscribe(self, user_id: str) -> None:
        self._subscribers.add(user_id)
        log.info(f"[browser-log] subscribed {user_id} (total={len(self._subscribers)})")
        # Despertar el loop si estaba idle
        if self._wake_event is not None:
            self._wake_event.set()

    def unsubscribe(self, user_id: str) -> None:
        self._subscribers.discard(user_id)
        log.info(f"[browser-log] unsubscribed {user_id} (total={len(self._subscribers)})")

    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="browser-log-watcher")
        log.info(f"[browser-log] watcher started on {self._log_path}")

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
        log.info("[browser-log] watcher stopped")

    async def _run(self) -> None:
        # Initial position: end of file (no replay de log antiguo)
        if self._log_path.exists():
            try:
                self._position = self._log_path.stat().st_size
            except Exception:
                self._position = 0

        while self._stop_event is not None and not self._stop_event.is_set():
            if not self._subscribers:
                # Esperar a stop O a wake_event (cuando llega un subscriber).
                # Si solo timeoutea, volver a chequear.
                self._wake_event.clear()
                done, _ = await asyncio.wait(
                    {
                        asyncio.create_task(self._stop_event.wait()),
                        asyncio.create_task(self._wake_event.wait()),
                    },
                    timeout=_IDLE_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._stop_event.is_set():
                    return
                continue

            await self._poll_and_emit()

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=_POLL_INTERVAL)
                return
            except asyncio.TimeoutError:
                pass

    async def _poll_and_emit(self) -> None:
        if not self._log_path.exists():
            return

        try:
            size = self._log_path.stat().st_size
        except Exception:
            return

        # Rotacion detectada: archivo encogio -> resetear posicion
        if size < self._position:
            self._position = 0

        if size == self._position:
            return

        try:
            chunk = await asyncio.to_thread(self._read_chunk_sync, size)
        except Exception as exc:
            log.warning(f"[browser-log] read failed: {exc}")
            return

        if not chunk:
            return

        for line in chunk.splitlines():
            if not line.strip():
                continue
            payload = {
                "type": "browser:log",
                "line": line,
                "ts": datetime.utcnow().isoformat() + "Z",
            }
            for user_id in list(self._subscribers):
                try:
                    await registry.push(user_id, payload)
                except Exception as exc:
                    log.debug(f"[browser-log] push to {user_id} failed: {exc}")

    def _read_chunk_sync(self, size: int) -> str:
        """Bloqueante, se ejecuta en thread para no parar el event loop."""
        try:
            with open(self._log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._position)
                chunk = f.read(size - self._position)
                self._position = f.tell()
        except Exception as exc:
            log.warning(f"[browser-log] open failed: {exc}")
            return ""
        return chunk


# Singleton
watcher = BrowserLogWatcher()
