"""
EventBus — Bus de eventos asyncio puro para Zephyro.

Pub/sub interno sin librerías externas. Los listeners son corrutinas
registradas por tipo de evento. publish() crea tareas individuales por
listener para nunca bloquear al productor.

Eventos estándar:
  GIT_COMMIT      — cambio en el codebase (git push/commit detectado)
  LOG_ERROR       — línea ERROR/WARNING detectada en el log del backend
  SKILL_EXECUTED  — skill invocada con resultado éxito/fallo
  ZEPHYRO_RESTART — reinicio del sistema
  AUTO_DIAGNOSE   — interno: LivingState → DiagnosticAgent auto-trigger
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

log = logging.getLogger("zephyro.event_bus")

# ── Constantes de tipos de evento ─────────────────────────────────────────────
GIT_COMMIT: str = "git_commit"
LOG_ERROR: str = "log_error"
SKILL_EXECUTED: str = "skill_executed"
AUTOMATION_EXECUTED: str = "automation_executed"
ZEPHYRO_RESTART: str = "zephyro_restart"
AUTO_DIAGNOSE: str = "auto_diagnose"


class EventBus:
    """
    Bus de pub/sub asyncio. Todos los métodos son thread-safe mediante
    `asyncio.get_event_loop().call_soon_threadsafe` cuando se llama desde
    threads de watchdog.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[..., Coroutine[Any, Any, None]]]] = {}

    def subscribe(
        self,
        event_type: str,
        coro: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        """Registra una corrutina como listener del evento dado."""
        self._listeners.setdefault(event_type, [])
        if coro not in self._listeners[event_type]:
            self._listeners[event_type].append(coro)

    def unsubscribe(
        self,
        event_type: str,
        coro: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        """Elimina un listener. No lanza error si no estaba registrado."""
        listeners = self._listeners.get(event_type, [])
        try:
            listeners.remove(coro)
        except ValueError:
            pass

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """
        Publica un evento. Crea una tarea asyncio por listener; no bloquea.
        Si un listener lanza excepción, se loguea y el bus continúa.
        Puede llamarse desde el hilo principal asyncio O desde un thread
        externo (watchdog) usando call_soon_threadsafe internamente.
        """
        listeners = list(self._listeners.get(event_type, []))
        if not listeners:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No hay loop corriendo en este thread — intentar el loop global
            loop = None

        for coro_fn in listeners:
            async def _safe_call(fn: Callable, p: dict) -> None:
                try:
                    await fn(p)
                except Exception as exc:
                    log.warning(
                        "[event_bus] Listener %s falló para evento '%s': %s",
                        getattr(fn, "__qualname__", repr(fn)),
                        event_type,
                        exc,
                    )

            if loop is not None:
                loop.create_task(_safe_call(coro_fn, payload))
            else:
                # Llamado desde thread sin loop → schedular en el loop principal
                try:
                    main_loop = asyncio.get_event_loop()
                    main_loop.call_soon_threadsafe(
                        lambda f=coro_fn, p=payload: main_loop.create_task(_safe_call(f, p))
                    )
                except RuntimeError:
                    log.warning("[event_bus] No se pudo publicar '%s': sin event loop", event_type)

    def publish_from_thread(self, event_type: str, payload: dict[str, Any]) -> None:
        """
        Versión explícita para llamar desde threads de watchdog.
        Usa call_soon_threadsafe para scheduling seguro en el loop asyncio.
        """
        listeners = list(self._listeners.get(event_type, []))
        if not listeners:
            return

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            log.warning("[event_bus] publish_from_thread: no hay event loop disponible")
            return

        for coro_fn in listeners:
            async def _safe_call(fn: Callable, p: dict) -> None:
                try:
                    await fn(p)
                except Exception as exc:
                    log.warning(
                        "[event_bus] Listener %s falló para '%s': %s",
                        getattr(fn, "__qualname__", repr(fn)),
                        event_type,
                        exc,
                    )

            loop.call_soon_threadsafe(
                lambda f=coro_fn, p=payload: loop.create_task(_safe_call(f, p))
            )


# ── Singleton global ──────────────────────────────────────────────────────────
_bus: EventBus | None = None


def get_bus() -> EventBus:
    """Retorna el singleton global del EventBus."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> None:
    """Resetea el singleton — útil en tests para aislar listeners."""
    global _bus
    _bus = None
