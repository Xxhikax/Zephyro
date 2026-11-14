#!/usr/bin/env python3
"""
BrowserController: Automatizacion web con Playwright/Edge (Chromium).

Arquitectura:
- Navegacion simple (abrir URLs/web apps) → Edge nativo del usuario
- Automatizacion compleja (buscar, click, escribir) → Playwright con Edge del sistema
- Usa perfil existente del usuario (sesiones logueadas en YouTube, Gmail, etc.)
- Variables de entorno de display configuradas automaticamente
- Sistema de estados, cola de comandos y monitor de salud
"""
import asyncio
import logging
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from core.browser_config import BrowserConfig, ensure_display_env, _get_active_edge_profile

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

# Configuracion de logging
ZEPHYRO_DEBUG = os.environ.get("ZEPHYRO_DEBUG", "").lower() in ("1", "true", "yes", "on")
DEBUG_LEVEL = logging.DEBUG if ZEPHYRO_DEBUG else logging.INFO

logger = logging.getLogger(__name__)
playwright_logger = logging.getLogger("playwright")

logger.setLevel(DEBUG_LEVEL)
playwright_logger.setLevel(DEBUG_LEVEL)


class BrowserState(Enum):
    """Estado de la maquina de estados del navegador"""
    CLOSED = "closed"
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"
    CLOSING = "closing"
    RECOVERING = "recovering"
    DEGRADED = "degraded"


class BrowserHealth(Enum):
    """Estado de salud del navegador"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


def debug_log(msg: str, *args, **kwargs):
    """Log detallado solo cuando ZEPHYRO_DEBUG esta activado"""
    if ZEPHYRO_DEBUG:
        logger.debug(f"[DEBUG] {msg}", *args, **kwargs)


def _is_closed(page) -> bool:
    """`page.is_closed()` tolerante: una Page muerta puede tirar al consultarla."""
    try:
        return bool(page.is_closed())
    except Exception:
        return True


# Busca, dentro del DOM actual (página del artista o resultados de búsqueda),
# el link de álbum (browse/MPRE...) cuyo título coincide con el nombre pedido.
# Normaliza (minúsculas, sin diacríticos, espacios colapsados) y prioriza el
# match exacto sobre el parcial. Devuelve el href o null. Esto reemplaza el
# "clickear la primera tarjeta", que reproducía el álbum equivocado cuando el
# filtro de YouTube Music rankeaba mal.
_ALBUM_MATCH_JS = r"""
(albumQuery) => {
    function norm(s){
        return (s||'').replace(/\s+/g,' ').trim().toLowerCase()
            .normalize('NFD').replace(/[̀-ͯ]/g,'');
    }
    const target = norm(albumQuery);
    if (!target) return null;
    const links = Array.from(
        document.querySelectorAll('a.yt-simple-endpoint[href*="browse/MPRE"]')
    );
    let exact = null, partial = null;
    for (const a of links) {
        const root = a.closest('ytmusic-two-row-item-renderer')
            || a.closest('ytmusic-responsive-list-item-renderer') || a;
        const titleEl = root.querySelector('.title, yt-formatted-string.title, [title]');
        let title = titleEl
            ? (titleEl.getAttribute('title') || titleEl.textContent)
            : a.textContent;
        title = norm(title);
        if (!title) continue;
        if (title === target) { exact = a.href; break; }
        if (!partial && (title.includes(target) || target.includes(title))) partial = a.href;
    }
    return exact || partial || null;
}
"""


class BrowserController:
    """
    Controlador de navegador usando Playwright.
    Mantiene una instancia persistente del navegador para multiples comandos.
    """

    def __init__(self, config: Optional[BrowserConfig] = None, user_profile: Optional[Any] = None):
        # Usar config proporcionada o crear una por defecto
        if config is None:
            config = BrowserConfig()
        self.config = config
        self.headless = config.headless
        # UserProfile opcional: si está presente, los plays musicales se loggean
        # en `interests` para alimentar el system prompt de Zephyro.
        self.user_profile = user_profile

        debug_log(f"BrowserController.__init__(config={config})")
        logger.info(
            f"Inicializando BrowserController: headless={config.headless}, "
            f"edge={config.edge_executable}, profile={config.user_profile_path}"
        )

        # Directorios
        self.screenshot_dir = Path(config.screenshot_dir)
        self.debug_dir = Path(config.debug_dir)
        self._setup_directories()
        self._cleanup_old_screenshots()

        # Cargar selectores
        self.selectors = self._load_selectors()

        # Estado del navegador
        self.playwright: Optional[Any] = None
        self.browser: Optional[Any] = None
        self.context: Optional[Any] = None
        self.page: Optional[Any] = None
        # Pestañas que abrimos NOSOTROS. En modo CDP, `self.context` es el
        # contexto real del usuario (`contexts[0]`), así que `context.pages`
        # incluye su Canvas, su mail y sus formularios a medio llenar. El
        # attach ya tiene cuidado — abre una pestaña nueva — pero `_close_tab`
        # y `_close_distraction_tabs_impl` ADOPTABAN una pestaña ajena al
        # cerrar la nuestra, y desde ahí todo `keyboard.press` / `goto` /
        # `click` caía en la página del usuario. Ver `_assert_own_page`.
        self._own_pages: set = set()

        # CDP attach: cuando estamos conectados al Edge real del usuario,
        # NO debemos cerrar contextos/tabs al hacer cleanup; solo desconectar.
        self._cdp_attached: bool = False
        self._edge_proc: Optional[subprocess.Popen] = None
        # True cuando el perfil dedicado de automatización se creó en este
        # arranque: el usuario debe iniciar sesión una vez en sus sitios.
        self._automation_profile_first_run: bool = False

        # Estado y control de concurrencia
        self._state: BrowserState = BrowserState.CLOSED
        self._health: BrowserHealth = BrowserHealth.UNKNOWN
        self._session_id: str = str(uuid.uuid4())[:8]
        self._state_lock: asyncio.Lock = asyncio.Lock()
        self._operation_lock: asyncio.Lock = asyncio.Lock()
        self._browser_lock: asyncio.Lock = asyncio.Lock()
        self._command_queue: Optional[BrowserController._CommandQueue] = None
        self._health_monitor: Optional[BrowserController._HealthMonitor] = None
        self._concurrent_operations: Set[str] = set()
        self._state_history: List[Dict[str, Any]] = []
        self._last_state_change: float = time.time()
        self._start_time: float = time.time()

        # Metricas
        self._metrics: Dict[str, Any] = {
            "commands_total": 0,
            "commands_success": 0,
            "commands_failed": 0,
            "restarts_total": 0,
            "errors_total": 0,
            "recovery_attempts": 0,
            "avg_command_time": 0.0,
        }

        # Configurar logging separado para playwright
        self._setup_playwright_logging()

        debug_log("BrowserController.__init__ completado")

    # =========================================================================
    # COLA DE COMANDOS
    # =========================================================================

    class _CommandQueue:
        """Cola de comandos para serializar operaciones del navegador"""

        def __init__(self, browser_controller):
            self._browser = browser_controller
            self._queue = asyncio.Queue()
            self._worker_task: Optional[asyncio.Task] = None
            self._running = False
            self._worker_lock = asyncio.Lock()
            self._queue_metrics = {
                "total_enqueued": 0,
                "total_processed": 0,
                "total_failed": 0,
                "avg_queue_time": 0.0,
                "avg_process_time": 0.0,
                "max_queue_size": 0,
            }
            self._current_command: Optional[Dict[str, Any]] = None

        async def start(self):
            async with self._worker_lock:
                if self._worker_task is not None and not self._worker_task.done():
                    return
                self._running = True
                self._worker_task = asyncio.create_task(self._worker_loop())
                debug_log("CommandQueue worker started")

        async def stop(self):
            async with self._worker_lock:
                self._running = False
                if self._worker_task is not None:
                    self._worker_task.cancel()
                    try:
                        await self._worker_task
                    except asyncio.CancelledError:
                        pass
                    self._worker_task = None
                debug_log("CommandQueue worker stopped")

        async def enqueue(self, command_func, *args, priority: int = 1,
                          command_name: Optional[str] = None, **kwargs) -> Any:
            if command_name is None:
                command_name = getattr(command_func, '__name__', 'unknown')

            command_id = f"{command_name}_{int(time.time() * 1000)}_{self._browser._session_id}"
            future = asyncio.Future()

            command = {
                "id": command_id,
                "func": command_func,
                "args": args,
                "kwargs": kwargs,
                "priority": priority,
                "future": future,
                "command_name": command_name,
                "enqueue_time": time.time(),
            }

            await self._queue.put(command)
            self._queue_metrics["total_enqueued"] += 1
            current_size = self._queue.qsize()
            if current_size > self._queue_metrics["max_queue_size"]:
                self._queue_metrics["max_queue_size"] = current_size

            debug_log(f"Command enqueued: {command_id}, queue_size={current_size}")

            try:
                return await future
            except asyncio.CancelledError:
                raise

        async def _worker_loop(self):
            debug_log("CommandQueue worker loop started")
            while self._running:
                try:
                    command = await self._queue.get()
                    self._current_command = command

                    command_id = command["id"]
                    command_name = command["command_name"]
                    start_time = time.time()
                    queue_time = start_time - command["enqueue_time"]

                    old_avg = self._queue_metrics["avg_queue_time"]
                    self._queue_metrics["avg_queue_time"] = old_avg * 0.9 + queue_time * 0.1

                    debug_log(f"Processing: {command_id}, queue_time={queue_time:.2f}s")

                    try:
                        result = await self._browser._operation_wrapper(
                            command["func"],
                            *command["args"],
                            command_name=command_name,
                            **command["kwargs"]
                        )

                        process_time = time.time() - start_time
                        old_avg = self._queue_metrics["avg_process_time"]
                        self._queue_metrics["avg_process_time"] = old_avg * 0.9 + process_time * 0.1

                        self._queue_metrics["total_processed"] += 1
                        command["future"].set_result(result)

                    except Exception as e:
                        self._queue_metrics["total_failed"] += 1
                        if not command["future"].done():
                            command["future"].set_exception(e)

                    finally:
                        self._current_command = None
                        self._queue.task_done()

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    debug_log(f"CommandQueue worker error: {e}")
                    await asyncio.sleep(1)

        def get_metrics(self) -> Dict[str, Any]:
            metrics = self._queue_metrics.copy()
            metrics["current_queue_size"] = self._queue.qsize()
            metrics["worker_running"] = self._running
            metrics["current_command"] = self._current_command["id"] if self._current_command else None
            return metrics

        def get_queue_size(self) -> int:
            return self._queue.qsize()

    # =========================================================================
    # MONITOR DE SALUD
    # =========================================================================

    class _HealthMonitor:
        """Monitor de salud que realiza verificaciones periodicas del navegador"""

        def __init__(self, browser_controller):
            self._browser = browser_controller
            self._monitor_task: Optional[asyncio.Task] = None
            self._running = False
            self._monitor_lock = asyncio.Lock()
            self._check_interval = 90  # Reducido de 30s: menor overhead en background
            self._max_consecutive_failures_before_fallback = 3
            self._fallback_cooldown = 300
            self._last_fallback_trigger_time = 0.0
            self._health_metrics = {
                "total_checks": 0,
                "failed_checks": 0,
                "consecutive_failures": 0,
                "last_check_time": 0.0,
                "avg_check_time": 0.0,
                "max_consecutive_failures": 0,
                "fallback_triggered": False,
                "headless_fallback_used": False,
                "estimated_memory_mb": 0,
            }
            self._last_successful_check = time.time()

        async def start(self):
            async with self._monitor_lock:
                if self._monitor_task is not None and not self._monitor_task.done():
                    return
                self._running = True
                self._monitor_task = asyncio.create_task(self._monitor_loop())
                debug_log("HealthMonitor started")

        async def stop(self):
            async with self._monitor_lock:
                self._running = False
                if self._monitor_task is not None:
                    self._monitor_task.cancel()
                    try:
                        await self._monitor_task
                    except asyncio.CancelledError:
                        pass
                    self._monitor_task = None
                debug_log("HealthMonitor stopped")

        async def _monitor_loop(self):
            while self._running:
                try:
                    start_time = time.time()
                    await self._perform_health_check()
                    check_time = time.time() - start_time

                    self._health_metrics["total_checks"] += 1
                    self._health_metrics["last_check_time"] = time.time()
                    old_avg = self._health_metrics["avg_check_time"]
                    self._health_metrics["avg_check_time"] = old_avg * 0.9 + check_time * 0.1
                    self._health_metrics["consecutive_failures"] = 0
                    self._last_successful_check = time.time()

                except Exception as e:
                    self._health_metrics["failed_checks"] += 1
                    self._health_metrics["consecutive_failures"] += 1
                    max_consec = self._health_metrics["max_consecutive_failures"]
                    if self._health_metrics["consecutive_failures"] > max_consec:
                        self._health_metrics["max_consecutive_failures"] = self._health_metrics["consecutive_failures"]

                    debug_log(f"Health check failed: {type(e).__name__}: {e!r}")

                    if (self._health_metrics["consecutive_failures"] >= self._max_consecutive_failures_before_fallback and
                        not self._health_metrics["headless_fallback_used"] and
                        (time.time() - self._last_fallback_trigger_time) > self._fallback_cooldown):
                        try:
                            await self.trigger_fallback(
                                f"consecutive_failures_{self._health_metrics['consecutive_failures']}"
                            )
                            self._last_fallback_trigger_time = time.time()
                            self._health_metrics["consecutive_failures"] = 0
                        except Exception as fallback_error:
                            debug_log(f"Fallback failed: {fallback_error}")

                try:
                    await asyncio.sleep(self._check_interval)
                except asyncio.CancelledError:
                    break

        async def _perform_health_check(self):
            if not self._browser.is_healthy():
                await self._browser._set_health(BrowserHealth.UNHEALTHY, "health_check_failed")
                raise RuntimeError("Browser is not healthy")

            try:
                if self._browser.page and not self._browser.page.is_closed():
                    await asyncio.wait_for(
                        self._browser.page.evaluate("1+1"),
                        timeout=5.0
                    )
            except (asyncio.TimeoutError, Exception) as e:
                await self._browser._set_health(BrowserHealth.DEGRADED, f"responsiveness_failed: {e}")
                raise RuntimeError(f"Responsiveness check failed: {e}")

            await self._check_memory_usage()

            current_state = self._browser.get_state()
            if current_state == BrowserState.DEGRADED:
                await self._browser._set_health(BrowserHealth.DEGRADED, "operational_but_degraded")
            else:
                await self._browser._set_health(BrowserHealth.HEALTHY, "health_check_passed")

        async def _check_memory_usage(self):
            try:
                if psutil is None:
                    return
                total_memory_mb = 0
                for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
                    try:
                        if 'msedge' in proc.info['name'].lower():
                            total_memory_mb += proc.memory_info().rss / (1024 * 1024)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                self._health_metrics["estimated_memory_mb"] = total_memory_mb
                # Bumped from 2000 to 4500 MB: Edge + YouTube Music routinely uses 3-5GB
                # Only truly excessive usage should trigger degradation.
                #
                # El WARNING se emite SOLO cuando realmente degrada (>6000 MB). Entre
                # 4500 y 6000 es normal con muchas pestañas y no hay nada que corregir:
                # como WARNING, living_state lo levantaba como "issue" y el
                # DiagnosticAgent lo servía como causa raíz en cada diagnóstico
                # (siempre "Edge supera el umbral"), tapando los errores de verdad.
                if total_memory_mb > 6000:
                    logger.warning(f"Edge memory critical: {total_memory_mb:.0f} MB")
                    await self._browser._set_health(BrowserHealth.DEGRADED, f"critical_memory_{total_memory_mb:.0f}mb")
                elif total_memory_mb > 3500:
                    debug_log(f"Edge memory elevated: {total_memory_mb:.0f} MB")
            except ImportError:
                pass
            except Exception as e:
                debug_log(f"Memory check error: {e}")

        async def trigger_fallback(self, reason: str):
            logger.warning(f"Triggering fallback: {reason}")
            if self._browser.headless:
                logger.warning("Already headless, no further fallback")
                return False

            # CDP mode: re-attach via CDP instead of headless fallback.
            # Headless loses cookies, login sessions, and current playback state.
            # Background recovery must NEVER kill/relaunch the user's real Edge:
            # allow_real_edge_relaunch=False limits recovery to the existing CDP
            # endpoint or the dedicated automation-profile instance.
            if self._browser._cdp_attached:
                logger.info("CDP mode: attempting re-attach (real endpoint or automation profile)")
                try:
                    await self._browser._close_browser_internal()
                    self._health_metrics["fallback_triggered"] = True
                    self._health_metrics["cdp_reattach_attempted"] = True
                    await self._browser._launch_cdp_attach(allow_real_edge_relaunch=False)
                    await self._browser._set_state(BrowserState.READY, f"cdp_reattach: {reason}")
                    await self._browser._set_health(BrowserHealth.HEALTHY, "cdp_reattach_ok")
                    logger.info("CDP re-attach successful")
                    return True
                except Exception as e:
                    logger.error(
                        f"All CDP recovery attempts failed: {type(e).__name__}: {e!r}"
                    )
                    await self._browser._set_state(
                        BrowserState.ERROR, f"cdp_all_fallbacks_failed: {type(e).__name__}"
                    )
                    await self._browser._set_health(BrowserHealth.UNHEALTHY, "cdp_all_fallbacks_failed")
                    return False

            # Non-CDP mode (standalone Playwright): fall back to headless
            logger.info("Attempting headless fallback...")
            self._health_metrics["fallback_triggered"] = True
            self._health_metrics["headless_fallback_used"] = True

            try:
                await self._browser._close_browser_internal()
                self._browser.headless = True
                self._browser.config.headless = True
                await self._browser._restart_browser()
                await self._browser._set_state(BrowserState.DEGRADED, f"headless_fallback: {reason}")
                await self._browser._set_health(BrowserHealth.DEGRADED, "headless_mode")
                logger.info("Headless fallback successful")
                return True
            except Exception as e:
                logger.error(f"Headless fallback failed: {e}")
                await self._browser._set_state(BrowserState.ERROR, f"fallback_failed: {e}")
                await self._browser._set_health(BrowserHealth.UNHEALTHY, "fallback_failed")
                return False

        def get_metrics(self) -> Dict[str, Any]:
            metrics = self._health_metrics.copy()
            metrics["monitor_running"] = self._running
            metrics["seconds_since_last_success"] = time.time() - self._last_successful_check
            return metrics

    # =========================================================================
    # ESTADO Y CONCURRENCIA
    # =========================================================================

    async def _set_state(self, new_state: BrowserState, reason: str = ""):
        async with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return

            self._state = new_state
            self._last_state_change = time.time()

            entry = {
                "timestamp": datetime.now().isoformat(),
                "old_state": old_state.value,
                "new_state": new_state.value,
                "reason": reason,
                "session_id": self._session_id,
            }
            self._state_history.append(entry)

            log_msg = f"State: {old_state.value} -> {new_state.value}"
            if reason:
                log_msg += f" ({reason})"
            logger.info(log_msg)

            if new_state == BrowserState.ERROR:
                self._metrics["errors_total"] += 1
            elif new_state == BrowserState.RECOVERING:
                self._metrics["recovery_attempts"] += 1

    def get_state(self) -> BrowserState:
        return self._state

    def get_health(self) -> BrowserHealth:
        return self._health

    async def _set_health(self, new_health: BrowserHealth, reason: str = ""):
        old_health = self._health
        if old_health == new_health:
            return
        self._health = new_health
        logger.info(f"Health: {old_health.value} -> {new_health.value}" + (f" ({reason})" if reason else ""))

    async def _record_command_start(self, command_name: str) -> str:
        command_id = f"{command_name}_{int(time.time() * 1000)}_{self._session_id}"
        async with self._state_lock:
            self._concurrent_operations.add(command_id)
            self._metrics["commands_total"] += 1
        return command_id

    async def _record_command_end(self, command_id: str, success: bool, duration: float):
        async with self._state_lock:
            self._concurrent_operations.discard(command_id)
            if success:
                self._metrics["commands_success"] += 1
            else:
                self._metrics["commands_failed"] += 1
            old_avg = self._metrics["avg_command_time"]
            self._metrics["avg_command_time"] = old_avg * 0.9 + duration * 0.1

    async def _operation_wrapper(self, coro_func, *args, command_name: str = "unknown", **kwargs):
        """Wrapper para operaciones que maneja estado, metricas y recuperacion."""
        command_id = await self._record_command_start(command_name)
        start_time = time.time()

        try:
            current_state = self.get_state()
            if current_state not in (BrowserState.READY, BrowserState.DEGRADED, BrowserState.STARTING):
                debug_log(f"Estado {current_state.value}, recuperando antes de {command_name}")
                await self._ensure_browser()

            async with self._operation_lock:
                result = await coro_func(*args, **kwargs)

            await self._record_command_end(command_id, True, time.time() - start_time)
            return result

        except Exception as e:
            duration = time.time() - start_time
            await self._record_command_end(command_id, False, duration)
            error_str = str(e)
            logger.error(f"Error en {command_name}: {error_str}")

            if "has been closed" in error_str or "Target page" in error_str:
                logger.warning(f"Browser cerrado en {command_name}, recuperando...")
                await self._set_state(BrowserState.RECOVERING, f"closed_during_{command_name}")
                try:
                    await self._restart_browser()
                    async with self._operation_lock:
                        result = await coro_func(*args, **kwargs)
                    await self._set_state(BrowserState.READY, "recovery_ok")
                    return result
                except Exception as recovery_error:
                    await self._set_state(BrowserState.ERROR, f"recovery_failed: {recovery_error}")
                    raise recovery_error
            else:
                await self._set_state(BrowserState.ERROR, f"failed: {error_str}")
                raise

    # =========================================================================
    # INICIALIZACION Y GESTION DE COLA/MONITOR
    # =========================================================================

    async def _init_command_queue(self):
        if self._command_queue is None:
            self._command_queue = self._CommandQueue(self)
            await self._command_queue.start()

    async def _stop_command_queue(self):
        if self._command_queue is not None:
            await self._command_queue.stop()
            self._command_queue = None

    async def _init_health_monitor(self):
        if self._health_monitor is None:
            self._health_monitor = self._HealthMonitor(self)
            await self._health_monitor.start()

    async def _stop_health_monitor(self):
        if self._health_monitor is not None:
            await self._health_monitor.stop()
            self._health_monitor = None

    async def _enqueue_command(self, command_func, *args, command_name: Optional[str] = None,
                               priority: Optional[int] = None, **kwargs) -> Any:
        await self._init_command_queue()
        assert self._command_queue is not None

        if priority is None:
            priority = self._get_command_priority(command_name or getattr(command_func, '__name__', 'unknown'))

        return await self._command_queue.enqueue(
            command_func, *args,
            priority=priority,
            command_name=command_name,
            **kwargs
        )

    def _get_command_priority(self, command_name: str) -> int:
        if command_name in ["close", "_close", "close_tab", "_close_tab",
                            "pause_music", "next_track", "prev_track", "mute_music", "stop_music"]:
            return 0
        if command_name in ["navigate_to", "_navigate_to", "open_and_search", "_open_and_search",
                           "play_media", "_play_media", "control_media", "_control_media"]:
            return 1
        return 2

    # =========================================================================
    # SETUP Y UTILIDADES
    # =========================================================================

    def _setup_directories(self):
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    def _cleanup_old_screenshots(self):
        try:
            now = time.time()
            cutoff = now - (self.config.screenshot_retention_days * 86400)
            files = list(self.screenshot_dir.glob("*.png"))
            deleted = 0
            for f in files:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1

            files = sorted(self.screenshot_dir.glob("*.png"), key=lambda x: x.stat().st_mtime)
            if len(files) > self.config.max_screenshot_files:
                for f in files[:len(files) - self.config.max_screenshot_files]:
                    f.unlink()

            playwright_logger.debug(f"Screenshots cleanup: {deleted} removed")
        except Exception as e:
            playwright_logger.warning(f"Error cleaning screenshots: {e}")

    def _load_selectors(self) -> Dict[str, Any]:
        selectors_path = Path(__file__).parent / "selectors.json"
        try:
            with open(selectors_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            playwright_logger.error(f"Error loading selectors: {e}")
            return {}

    def _setup_playwright_logging(self):
        from logging.handlers import RotatingFileHandler

        for handler in playwright_logger.handlers[:]:
            playwright_logger.removeHandler(handler)

        handler = RotatingFileHandler(
            self.config.log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        handler.setLevel(DEBUG_LEVEL)
        playwright_logger.addHandler(handler)
        playwright_logger.setLevel(DEBUG_LEVEL)

        if ZEPHYRO_DEBUG:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            console_handler.setLevel(DEBUG_LEVEL)
            playwright_logger.addHandler(console_handler)

    # =========================================================================
    # NUCLEO: LANZAMIENTO Y GESTION DEL NAVEGADOR (SIMPLIFICADO)
    # =========================================================================

    async def _restart_browser(self):
        """Reinicia completamente el navegador via CDP attach al Edge real del usuario."""
        debug_log(f"_restart_browser() called")
        logger.info("Reiniciando navegador Playwright...")
        await self._set_state(BrowserState.STARTING, "restart_initiated")
        await self._set_health(BrowserHealth.UNKNOWN, "restart_in_progress")

        # Limpiar estado previo
        await self._close_browser_internal()

        # Asegurar variables de entorno de display
        ensure_display_env()

        last_error = None
        strategies = self._build_launch_strategies()

        for strategy_name, launch_func in strategies:
            try:
                debug_log(f"Intentando estrategia: {strategy_name}")
                logger.info(f"Lanzando Edge: {strategy_name}")

                self.playwright = await async_playwright().start()
                await launch_func()

                # Verificar que tenemos una pagina funcional
                assert self.page is not None
                assert not self.page.is_closed()

                playwright_logger.info(f"Edge lanzado ({strategy_name}, headless={self.headless})")
                logger.info(f"Edge listo: {strategy_name}")

                final_state = BrowserState.READY if not self.headless else BrowserState.DEGRADED
                await self._set_state(final_state, f"started_{strategy_name}")
                await self._set_health(BrowserHealth.HEALTHY, "browser_operational")
                self._metrics["restarts_total"] += 1

                # Iniciar monitor de salud
                try:
                    await self._init_health_monitor()
                except Exception as e:
                    debug_log(f"Error starting health monitor: {e}")

                return  # Exito

            except Exception as e:
                last_error = e
                logger.warning(f"Estrategia {strategy_name} fallo: {type(e).__name__}: {e!r}")
                debug_log(traceback.format_exc())
                # Limpiar para siguiente intento
                await self._close_browser_internal()

        # Todas las estrategias fallaron
        error_str = str(last_error).lower()
        _binary_patterns = (
            "executable doesn't exist",
            "executable not found",
            "failed to launch",
            "browsertype.launch",
            "no such file or directory",
            "error launching browser",
        )
        if any(p in error_str for p in _binary_patterns):
            friendly = (
                "No se encontraron los binarios de Playwright. "
                "Ejecuta: playwright install chromium"
            )
            logger.error(friendly)
            await self._set_state(BrowserState.ERROR, "binary_missing")
            await self._set_health(BrowserHealth.UNHEALTHY, "binary_missing")
            raise RuntimeError(friendly)

        error_msg = f"No se pudo lanzar Edge: {type(last_error).__name__}: {last_error!r}"
        logger.error(error_msg)
        await self._set_state(BrowserState.ERROR, error_msg)
        await self._set_health(BrowserHealth.UNHEALTHY, "all_strategies_failed")
        raise RuntimeError(error_msg)

    def _build_launch_strategies(self) -> list:
        """
        Estrategia única: CDP attach (Edge real o perfil dedicado).

        Orden interno de _launch_cdp_attach:
        1. Edge real con debug port expuesto → attach directo (sesión completa).
        2. Instancia dedicada de automatización (perfil persistente separado,
           puerto automation_cdp_port) — el Edge real nunca se cierra.
        3. Fallback (solo en flujos iniciados por el usuario): relanzar el
           Edge real con debug port preservando su perfil activo.
        """
        config = self.config
        if not (config.edge_executable and Path(config.edge_executable).exists()):
            raise RuntimeError(
                "No se encontró el ejecutable de Microsoft Edge. "
                "Instálalo o configura BrowserConfig.edge_executable."
            )
        return [("cdp_attach_real_edge", self._launch_cdp_attach)]

    @staticmethod
    def _is_cdp_port_alive(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
        """Comprueba si hay un endpoint CDP escuchando en el puerto."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (OSError, socket.timeout):
            return False

    @staticmethod
    def _is_edge_running() -> bool:
        """Detecta si hay un proceso de Edge corriendo en el sistema."""
        try:
            if sys.platform == "win32":
                out = subprocess.check_output(
                    ["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/NH"],
                    text=True, errors="ignore", stderr=subprocess.DEVNULL,
                )
                return "msedge.exe" in out.lower()
            else:
                return subprocess.run(
                    ["pgrep", "-x", "msedge"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ).returncode == 0
        except Exception:
            return False

    @staticmethod
    def _kill_edge_processes(force: bool = False) -> None:
        """Cierra procesos de Edge. Sin force, intenta cierre graceful primero."""
        try:
            if sys.platform == "win32":
                cmd = ["taskkill", "/F", "/IM", "msedge.exe", "/T"] if force \
                      else ["taskkill", "/IM", "msedge.exe", "/T"]
            else:
                cmd = ["pkill", "-f", "msedge"] if force else ["pkill", "msedge"]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception as e:
            logger.warning(f"No se pudo cerrar Edge: {e}")

    @staticmethod
    async def _wait_for_edge_exit(timeout: float = 10.0, poll_interval: float = 0.3) -> bool:
        """
        Espera a que todos los procesos de Edge terminen.
        Retorna True si salieron dentro del timeout, False si siguen corriendo.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not await asyncio.to_thread(BrowserController._is_edge_running):
                return True
            await asyncio.sleep(poll_interval)
        return False

    async def _launch_edge_with_debug_port(self) -> None:
        """
        Lanza Edge como subproceso con --remote-debugging-port apuntando al
        perfil real del usuario. Espera a que el puerto CDP esté listo.
        """
        config = self.config
        if not config.edge_executable:
            raise RuntimeError("No se encontró el ejecutable de Microsoft Edge.")
        if not config.user_profile_path or not Path(config.user_profile_path).exists():
            raise RuntimeError(
                f"Perfil real de Edge no encontrado en {config.user_profile_path}"
            )

        cdp_port = config.cdp_port
        # Preservar el perfil activo del usuario: sin --profile-directory,
        # Edge abre "Default" aunque el usuario estuviera en otro perfil.
        active_profile = _get_active_edge_profile(config.user_profile_path)
        args = [
            config.edge_executable,
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={config.user_profile_path}",
            f"--profile-directory={active_profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--restore-last-session",
        ]
        if self.headless:
            args.append("--headless=new")

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        logger.info(
            f"Lanzando Edge con CDP en puerto {cdp_port}, perfil real del usuario "
            f"(--profile-directory={active_profile})"
        )
        self._edge_proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        # Esperar a que el puerto CDP esté listo
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if await asyncio.to_thread(self._is_cdp_port_alive, cdp_port):
                return
            await asyncio.sleep(0.3)
        raise RuntimeError(
            f"Edge no expuso el puerto CDP {cdp_port} en 20s. "
            "¿Quizá había otra instancia de Edge bloqueando el perfil?"
        )

    async def _launch_edge_automation_profile(self) -> None:
        """
        Lanza una segunda instancia de Edge con el perfil dedicado de
        automatización (automation_profile_path + automation_cdp_port).

        Nunca toca el Edge ni el perfil real del usuario: el lock de
        User Data es por directorio, así que ambas instancias conviven.
        El perfil persiste entre sesiones (logins guardados tras el primer
        inicio de sesión manual del usuario).
        """
        config = self.config
        if not config.edge_executable:
            raise RuntimeError("No se encontró el ejecutable de Microsoft Edge.")
        if not config.automation_profile_path:
            raise RuntimeError("automation_profile_path no configurado.")

        auto_dir = Path(config.automation_profile_path)
        first_run = not auto_dir.exists()
        auto_dir.mkdir(parents=True, exist_ok=True)

        cdp_port = config.automation_cdp_port
        args = [
            config.edge_executable,
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={auto_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--restore-last-session",
        ]
        if self.headless:
            args.append("--headless=new")

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        logger.info(
            f"Lanzando Edge con perfil dedicado de automatización: {auto_dir} "
            f"(puerto {cdp_port})"
        )
        self._edge_proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        deadline = time.time() + 20.0
        while time.time() < deadline:
            if await asyncio.to_thread(self._is_cdp_port_alive, cdp_port):
                if first_run:
                    self._automation_profile_first_run = True
                    logger.info(
                        "Perfil de automatización creado por primera vez. "
                        "El usuario debe iniciar sesión una vez en sus sitios "
                        "(Instagram, YouTube Music, etc.) en esta instancia."
                    )
                return
            await asyncio.sleep(0.3)
        raise RuntimeError(
            f"Edge (perfil automatización) no expuso el puerto CDP {cdp_port} en 20s."
        )

    async def _launch_cdp_attach(self, allow_real_edge_relaunch: bool = False):
        """
        Conecta a Edge vía CDP sin tocar el Edge real del usuario.

        Orden de attach:
        1. Si el puerto CDP del Edge real responde, attach directo (mejor
           caso: sesión completa del usuario).
        2. Si no, usa/lanza la instancia dedicada de automatización (perfil
           separado en automation_profile_path, puerto automation_cdp_port).
           El Edge real NUNCA se cierra en este camino.
        3. Solo si la instancia dedicada falla y allow_real_edge_relaunch es
           True: cierre graceful del Edge real, espera de terminación y
           relanzamiento con debug port preservando el perfil activo.
        4. connect_over_cdp con reintentos y apertura de pestaña propia.

        **`allow_real_edge_relaunch` ahora es False por defecto** (2026-10-03).
        Era True, y `_restart_browser` llama sin argumentos: o sea que un
        "abre instagram" podía CERRAR Y RELANZAR el navegador del usuario, con
        sus pestañas, para poder automatizar. Matar el navegador de alguien es
        un efecto mucho más grande que el comando que lo pidió. Ahora, si el
        attach y el perfil dedicado fallan, la automatización falla de forma
        controlada y se dice; relanzar el Edge real necesita pedido explícito.
        """
        assert self.playwright is not None
        config = self.config

        target_port = config.cdp_port
        if not await asyncio.to_thread(self._is_cdp_port_alive, config.cdp_port):
            if await asyncio.to_thread(self._is_cdp_port_alive, config.automation_cdp_port):
                # La instancia dedicada ya está corriendo de antes
                target_port = config.automation_cdp_port
            else:
                try:
                    await self._launch_edge_automation_profile()
                    target_port = config.automation_cdp_port
                except Exception as exc:
                    logger.error(
                        f"Instancia dedicada de automatización falló: "
                        f"{type(exc).__name__}: {exc!r}"
                    )
                    if not allow_real_edge_relaunch:
                        raise
                    logger.warning(
                        "Fallback: relanzando el Edge real con debug port "
                        "(tus pestañas se restaurarán automáticamente)."
                    )
                    if await asyncio.to_thread(self._is_edge_running):
                        await asyncio.to_thread(self._kill_edge_processes, False)
                        if not await self._wait_for_edge_exit(timeout=10.0):
                            logger.warning(
                                "Cierre graceful falló. Forzando cierre (tus pestañas "
                                "se restaurarán automáticamente al relanzar)."
                            )
                            await asyncio.to_thread(self._kill_edge_processes, True)
                            await self._wait_for_edge_exit(timeout=5.0)
                    await self._launch_edge_with_debug_port()
                    target_port = config.cdp_port

        cdp_url = f"http://127.0.0.1:{target_port}"
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"connect_over_cdp intento {attempt + 1}/3 falló: "
                    f"{type(exc).__name__}: {exc!r}"
                )
                if attempt < 2:
                    await asyncio.sleep(2.0 * (attempt + 1))
        else:
            assert last_exc is not None
            raise last_exc
        assert self.browser is not None

        contexts = self.browser.contexts
        self.context = contexts[0] if contexts else await self.browser.new_context()
        assert self.context is not None

        # Abrir SIEMPRE una pestaña nueva: nunca pisamos las pestañas que el
        # usuario ya tenga abiertas. Reutilizar la primera existente provoca
        # races y borra contexto del usuario.
        self.page = await self.context.new_page()
        self._register_own_page(self.page)

        self._cdp_attached = True
        profile_desc = (
            config.user_profile_path if target_port == config.cdp_port
            else config.automation_profile_path
        )
        logger.info(
            f"CDP attach OK: perfil {profile_desc} "
            f"(puerto {target_port}, contexts={len(contexts)})"
        )

    # ── Propiedad de la pestaña ─────────────────────────────────────────
    # `self.page` solo puede ser una pestaña que abrimos nosotros. Es una
    # invariante estructural: las únicas asignaciones válidas son
    # `context.new_page()` y `None`.
    #
    # Antes NO se cumplía, y por dos líneas: `_close_tab` hacía
    # `self.page = pages[-2]` y `_close_distraction_tabs_impl`
    # `self.page = remaining[-1]`, los dos sobre `self.context.pages`. En modo
    # CDP ese contexto es el del usuario, así que después de cerrar una
    # pestaña `self.page` apuntaba a SU Canvas o SU mail — y de ahí en
    # adelante cada `keyboard.press('k')`, `keyboard.type()`, `goto()` y
    # `click()` del controller caía en su página. Ese es el mecanismo por el
    # que un comando mal ruteado le tipeó una `k` a Canvas (ea22754).

    def _register_own_page(self, page) -> None:
        # Se podan las cerradas para que el set no crezca sin límite.
        self._own_pages = {p for p in self._own_pages if not _is_closed(p)}
        self._own_pages.add(page)

    def _owns_current_page(self) -> bool:
        return self.page is not None and self.page in self._own_pages

    def _release_page_if_foreign(self) -> None:
        """Suelta `self.page` si no es nuestra; `_ensure_browser` la recrea."""
        if self.page is not None and not self._owns_current_page():
            logger.warning(
                "self.page no es una pestaña nuestra — se suelta en vez de "
                "operar sobre la del usuario"
            )
            self.page = None

    async def _close_browser_internal(self):
        """Cierra todo sin cambiar estado (uso interno)."""
        try:
            if self._cdp_attached:
                # CDP attach: solo cerramos NUESTRA pestaña y desconectamos.
                # NO cerrar el contexto: comparte tabs con el usuario.
                # NO matar el proceso de Edge: el usuario lo está usando.
                if self.page is not None:
                    try:
                        await self.page.close()
                    except Exception:
                        pass
                if self.browser is not None:
                    try:
                        # browser.close() en modo CDP solo desconecta el cliente,
                        # no cierra el navegador del usuario.
                        await self.browser.close()
                    except Exception:
                        pass
            else:
                # Modo legacy (no usado actualmente, conservado por defensa)
                if self.context:
                    try:
                        await self.context.close()
                    except Exception:
                        pass
                elif self.browser:
                    try:
                        await self.browser.close()
                    except Exception:
                        pass
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
        finally:
            self.browser = None
            self.context = None
            self.page = None
            self.playwright = None
            self._cdp_attached = False

    async def _ensure_browser(self):
        """Asegura que el navegador este abierto y saludable."""
        debug_log(f"_ensure_browser: pw={self.playwright is not None}, br={self.browser is not None}, ctx={self.context is not None}, pg={self.page is not None}")

        async with self._browser_lock:
            # Punto único donde se hace cumplir la propiedad de la pestaña.
            # TODA operación del controller pasa por acá (`_operation_wrapper`
            # → `_ensure_browser`), así que con soltar la ajena en este lugar
            # ninguna acción puede caer en una página del usuario, aunque
            # alguien agregue mañana una asignación nueva a `self.page`.
            self._release_page_if_foreign()

            current_state = self.get_state()
            if current_state in (BrowserState.CLOSED, BrowserState.ERROR, BrowserState.RECOVERING):
                await self._set_state(BrowserState.STARTING, "ensure_browser")
                await self._set_health(BrowserHealth.UNKNOWN, "checking")

            try:
                # Verificar cadena completa
                # Nota: en modo contexto persistente, self.browser puede ser None
                # pero self.context sigue siendo válido
                if self.playwright is None or (self.browser is None and self.context is None):
                    await self._restart_browser()
                    return

                # Solo verificar browser.is_connected() si tenemos un browser explícito
                browser_connected = True
                if self.browser is not None:
                    try:
                        browser_connected = self.browser.is_connected()
                    except Exception:
                        browser_connected = False

                if not browser_connected:
                    logger.warning("Browser desconectado, reiniciando...")
                    await self._restart_browser()
                    return

                # Verificar contexto
                if self.context is None or not await self._is_context_alive(self.context):
                    logger.warning("Context muerto, recreando...")
                    try:
                        if self.context:
                            await self.context.close()
                    except Exception:
                        pass
                    try:
                        assert self.browser is not None
                        self.context = await self.browser.new_context(viewport=self.config.viewport)
                        self.page = None
                    except Exception as e:
                        logger.warning(f"Error recreando context: {e}, reiniciando completo")
                        await self._restart_browser()
                        return

                # Verificar pagina
                if self.page is None or self.page.is_closed():
                    assert self.context is not None
                    self.page = await self.context.new_page()
                    self._register_own_page(self.page)
                    logger.info("Nueva pagina creada")

                # Verificacion final
                if self.page and not self.page.is_closed():
                    state = BrowserState.READY if not self.headless else BrowserState.DEGRADED
                    await self._set_state(state, "browser_ready")
                    await self._set_health(BrowserHealth.HEALTHY, "all_checks_passed")
                    return

                raise RuntimeError("No se pudo establecer pagina valida")

            except Exception as e:
                logger.error(f"Error en _ensure_browser: {e}")
                try:
                    await self._restart_browser()
                except Exception as restart_error:
                    logger.critical(f"Fallo catastrofico: {restart_error}")
                    raise

    async def _is_context_alive(self, context) -> bool:
        if context is None:
            return False
        try:
            # .pages es una propiedad sincrona: no crea objetos nuevos
            _ = context.pages
            return True
        except Exception:
            return False

    def is_healthy(self) -> bool:
        try:
            if self.playwright is None or self.browser is None or not self.browser.is_connected():
                return False
            if self.context is None or self.page is None or self.page.is_closed():
                return False
            return True
        except Exception:
            return False

    # =========================================================================
    # HELPERS DE INTERACCION
    # =========================================================================

    async def _handle_cookie_popups(self):
        """Intenta cerrar popups de cookies si estan presentes"""
        try:
            assert self.page is not None
            cookie_selectors = [
                "button[aria-label*='Accept']",
                "button:has-text('Accept')",
                "button:has-text('Aceptar')",
                "button[aria-label*='Aceptar']",
                "ytd-button-renderer:has-text('Accept')",
                "ytd-button-renderer:has-text('Aceptar')",
                # "#content ytd-button-renderer" se quitó el 2026-10-01: no
                # filtraba por texto, así que clickeaba CUALQUIER botón dentro
                # de #content. Las entradas de arriba ya cubren el banner de
                # cookies de YouTube por su texto.
                "button:has-text('Accept all')",
                "button:has-text('Aceptar todo')",
            ]

            for selector in cookie_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        await element.click()
                        await asyncio.sleep(0.5)
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    async def _find_element(self, provider: str, element_type: str, timeout: float = 30.0):
        """Busca elemento usando multiples selectores con fallback"""
        if provider not in self.selectors or element_type not in self.selectors[provider]:
            raise ValueError(f"No selectores para {provider}.{element_type}")

        assert self.page is not None

        if element_type == "search_box":
            await self._handle_cookie_popups()

        selectors = self.selectors[provider][element_type]
        per_selector_timeout = max(1500, min(15000, int(timeout * 1000 / max(len(selectors), 1))))

        for selector in selectors:
            try:
                element = await self.page.wait_for_selector(selector, timeout=per_selector_timeout)
                if element:
                    debug_log(f"Found {element_type} with: {selector}")
                    return element
            except Exception:
                continue

        # Fallback: buscar por links si es video/song item
        if element_type in ["video_item", "song_item"]:
            try:
                all_links = await self.page.query_selector_all("a")
                for link in all_links:
                    text = await link.inner_text()
                    if text and len(text.strip()) > 0:
                        return link
            except Exception:
                pass

        raise Exception(f"No se pudo encontrar {element_type} despues de {timeout}s")

    async def _take_debug_screenshot(self, error_context: str = ""):
        """Toma screenshot de depuracion"""
        timestamp = int(time.time())
        # Sanitizar nombre de archivo
        safe_context = "".join(c for c in error_context[:50] if c.isalnum() or c in "._-")
        filename = f"error_{timestamp}_{safe_context}.png"
        path = self.screenshot_dir / filename
        try:
            assert self.page is not None
            await self.page.screenshot(path=str(path))
            playwright_logger.error(f"Error screenshot: {path}")
            return str(path)
        except Exception as e:
            playwright_logger.error(f"Error taking screenshot: {e}")
            return None

    @staticmethod
    def _validate_url(url: str) -> str:
        """Valida y normaliza una URL."""
        if not url or not url.strip():
            raise ValueError("URL vacia")

        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        parsed = urlparse(url)
        if not parsed.netloc or parsed.netloc == "about:blank":
            raise ValueError(f"URL invalida: {url}")

        return url

    # =========================================================================
    # ACCIONES PUBLICAS (wrappers que encolan)
    # =========================================================================

    async def navigate_to(self, url: str) -> str:
        return await self._enqueue_command(self._navigate_to, url, command_name="navigate_to")

    async def open_and_search(self, query: str, search_engine: str = "duckduckgo") -> str:
        return await self._enqueue_command(self._open_and_search, query, search_engine, command_name="open_and_search")

    async def click(self, selector: str) -> str:
        return await self._enqueue_command(self._click, selector, command_name="click")

    async def click_element(self, element_type: str, provider: str = "generic") -> str:
        return await self._enqueue_command(self._click_element, element_type, provider, command_name="click_element")

    async def type_text(self, selector: str, text: str) -> str:
        return await self._enqueue_command(self._type_text, selector, text, command_name="type_text")

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        return await self._enqueue_command(self._scroll, direction, amount, command_name="scroll")

    async def get_text(self, selector: Optional[str] = None) -> str:
        return await self._enqueue_command(self._get_text, selector, command_name="get_text")

    async def screenshot(self) -> str:
        return await self._enqueue_command(self._screenshot, command_name="screenshot")

    async def close(self) -> str:
        return await self._enqueue_command(self._close, command_name="close")

    async def play_media(self, query: str, provider: str = "youtube") -> str:
        return await self._enqueue_command(self._play_media, query, provider, command_name="play_media")

    async def play_artist(self, name: str, mode: str = "shuffle") -> str:
        return await self._enqueue_command(self._play_yt_music_artist, name, mode, command_name="play_artist")

    async def play_album(self, query: str) -> str:
        return await self._enqueue_command(self._play_yt_music_album, query, command_name="play_album")

    async def play_best_album_for_artist(self, artist: str) -> str:
        """Navega a la página del artista y reproduce su álbum más popular."""
        return await self._enqueue_command(self._play_best_album_for_artist, artist, command_name="play_best_album_for_artist")

    async def play_named_album(self, artist: str, album: str, fallback_query: Optional[str] = None) -> str:
        """Reproduce un álbum específico por nombre, vía la página del artista."""
        return await self._enqueue_command(
            self._play_named_album_for_artist, artist, album, fallback_query,
            command_name="play_named_album",
        )

    async def play_recommendations(self, seed: Optional[str] = None) -> str:
        return await self._enqueue_command(self._play_yt_music_recommendations, seed, command_name="play_recommendations")

    async def pause_music(self) -> str:
        return await self._enqueue_command(self._pause_media, command_name="pause_music")

    async def resume_music(self) -> str:
        return await self._enqueue_command(self._resume_media, command_name="resume_music")

    async def next_track(self) -> str:
        return await self._enqueue_command(self._next_track, command_name="next_track")

    async def prev_track(self) -> str:
        return await self._enqueue_command(self._prev_track, command_name="prev_track")

    async def mute_music(self) -> str:
        return await self._enqueue_command(self._toggle_mute, command_name="mute_music")

    async def stop_music(self) -> str:
        return await self._enqueue_command(self._stop_music, command_name="stop_music")

    async def control_media(self, action: str) -> str:
        return await self._enqueue_command(self._control_media, action, command_name="control_media")

    async def wait_for(self, element_type: str, provider: str = "generic", timeout: float = 10.0) -> str:
        return await self._enqueue_command(self._wait_for, element_type, provider, timeout, command_name="wait_for")

    async def find_by_text(self, text: str, partial: bool = True) -> str:
        return await self._enqueue_command(self._find_by_text, text, partial, command_name="find_by_text")

    async def close_tab(self) -> str:
        # Guard: si no hay browser abierto o está en estado de error/recuperación, no hay nada que cerrar.
        # IMPORTANTE: cubrir ERROR y RECOVERING además de CLOSED — de lo contrario _operation_wrapper
        # llama a _ensure_browser() y lanza Firefox innecesariamente cuando el browser falló.
        if self._state in (BrowserState.CLOSED, BrowserState.ERROR, BrowserState.RECOVERING):
            playwright_logger.info(f"CLOSE_TAB | estado {self._state.value}, skipping")
            return "No hay navegador abierto"
        return await self._enqueue_command(self._close_tab, command_name="close_tab")

    async def close_distraction_tabs(self, url_pattern: str) -> str:
        """Cierra todas las tabs cuya URL contenga url_pattern (enforcement de foco)."""
        if self._state in (BrowserState.CLOSED, BrowserState.ERROR, BrowserState.RECOVERING):
            return "No hay navegador abierto"
        return await self._enqueue_command(
            self._close_distraction_tabs_impl,
            url_pattern,
            command_name="close_distraction_tabs",
        )

    async def _close_distraction_tabs_impl(self, url_pattern: str) -> str:
        """Cierra las pestañas que matcheen un patrón de URL.

        Es la ÚNICA acción con efecto destructivo que Zephyro dispara sin que
        el usuario diga nada: la lanza el detector de distracciones de
        `modules/schedule/monitor.py` por temporizador
        (`_enforce_browser_close`), al vencer `enforcement_deadline`.

        Por eso cierra con ``run_before_unload=True``. Antes era
        ``page.close()`` pelado, que OMITE el handler `beforeunload` — o sea
        que se llevaba un formulario a medio llenar, un comentario largo o un
        checkout sin el diálogo "¿seguro que querés salir?" que el usuario
        habría visto si cerraba la pestaña con la X. Con el flag, el navegador
        corre el handler y Playwright descarta el diálogo por default, así que
        una página con cambios sin guardar NO se cierra.

        Devuelve cuántas cerró y cuántas se resistieron, y no cuenta como
        cerrada ninguna que siga viva: `close(run_before_unload=True)` no
        espera a que la página se cierre, así que hay que verificar el estado
        en vez de asumirlo.
        """
        if self.context is None:
            return "No hay contexto de browser"
        pattern = (url_pattern or "").strip().lower()
        if not pattern:
            # "" is a substring of every URL: it would close every tab, including the
            # user's own in CDP mode.
            return "0 pestaña(s) cerrada(s) (patrón vacío)"
        closed = 0
        kept = 0
        for page in list(self.context.pages):
            try:
                if pattern not in (page.url or "").lower():
                    continue
                was_ours = page is self.page
                await page.close(run_before_unload=True)
                if _is_closed(page):
                    # Recién acá se suelta: si la que cerró era la nuestra, se
                    # pone en None en vez de adoptar otra del contexto — que en
                    # modo CDP es una pestaña del usuario. `_ensure_browser`
                    # abre una nueva cuando haga falta.
                    if was_ours:
                        self.page = None
                    self._own_pages.discard(page)
                    closed += 1
                else:
                    # La página se resistió: tiene `beforeunload`, o sea
                    # trabajo sin guardar. Se respeta y se deja como estaba.
                    kept += 1
            except Exception:
                pass
        playwright_logger.info(
            f"CLOSE_DISTRACTION_TABS | pattern={url_pattern!r} | "
            f"closed={closed} kept_unsaved={kept}"
        )
        if kept:
            return (
                f"{closed} pestaña(s) cerrada(s) ({url_pattern}). "
                f"Dejé {kept} abierta(s): tienen cambios sin guardar."
            )
        return f"{closed} pestaña(s) cerrada(s) ({url_pattern})"

    # =========================================================================
    # IMPLEMENTACION DE ACCIONES
    # =========================================================================

    async def _navigate_to(self, url: str) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None

            url = self._validate_url(url)
            debug_log(f"NAVIGATE_TO: {url}")

            await self.page.goto(url, timeout=self.config.timeout_navigation, wait_until="domcontentloaded")

            # Esperar networkidle solo si la pagina lo alcanza rapidamente (SPAs/streaming lo ignoran)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass  # networkidle puede fallar en paginas con streaming

            title = await self.page.title()
            logger.debug(f"NAVIGATE_TO: ok, titulo '{title}'")
            await self._bring_window_to_front(title)
            return f"He abierto {url} ({title})"

        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"NAVIGATE_TO error: {e}")
            try:
                await self._take_debug_screenshot(f"nav_error_{url[:30]}")
            except Exception:
                pass
            return f"Error navegando: {e}"

    async def _bring_window_to_front(self, page_title: str = "") -> bool:
        """Trae al frente la ventana del navegador de automatización.

        Sin esto, una navegación explícita ("abre reels de instagram") ocurre en
        el perfil dedicado (EdgeAutomation, puerto CDP propio) y el usuario, que
        está mirando su Edge normal, no ve ningún cambio: el comando "funciona"
        pero parece ignorado.

        Se llama SOLO desde ``_navigate_to`` (navegación pedida explícitamente).
        Los comandos de media no lo usan a propósito: reproducir música en
        background no debe robarle el foco a lo que el usuario esté haciendo.

        Nunca lanza: si el foco falla, la navegación ya fue exitosa.
        """
        ok = False
        try:
            if self.page is not None:
                await self.page.bring_to_front()
                ok = True
        except Exception as exc:
            debug_log(f"bring_to_front (Playwright) falló: {exc}")

        # Playwright activa la pestaña dentro del navegador, pero no levanta la
        # ventana del SO si está minimizada o detrás. Eso lo hace Win32.
        try:
            from core.platform import focus_window
            # `to_thread`: `_focus_window_windows` barre hasta 500 ventanas
            # top-level leyendo el título de cada una, y `GetWindowTextW` sobre
            # una ventana ajena es un envío cross-process al thread que la
            # posee. Si ESE thread está colgado, la llamada se bloquea — con el
            # event loop adentro si corre acá.
            if await asyncio.to_thread(
                focus_window, "msedge", None, page_title or None
            ):
                ok = True
        except Exception as exc:
            debug_log(f"focus_window (SO) falló: {exc}")

        if not ok:
            logger.debug("No pude traer la ventana del navegador al frente")
        return ok

    async def _open_and_search(self, query: str, engine: str) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None

            if engine == "google":
                await self.page.goto("https://www.google.com", timeout=self.config.timeout_navigation)
                await self.page.fill("textarea[name='q']", query)
                await self.page.press("textarea[name='q']", "Enter")
            elif engine == "youtube":
                current_url = self.page.url
                if not current_url.startswith("https://www.youtube.com"):
                    await self.page.goto("https://www.youtube.com", timeout=self.config.timeout_navigation)
                    await self.page.wait_for_load_state("domcontentloaded")
                search_box = await self._find_element("youtube", "search_box")
                await search_box.fill(query)
                await search_box.press("Enter")
            elif engine == "youtube_music":
                current_url = self.page.url
                if not current_url.startswith("https://music.youtube.com"):
                    await self.page.goto("https://music.youtube.com", timeout=self.config.timeout_navigation)
                    await self.page.wait_for_load_state("domcontentloaded")
                search_box = await self._find_element("youtube_music", "search_box")
                await search_box.fill(query)
                await search_box.press("Enter")
            else:
                await self.page.goto("https://duckduckgo.com", timeout=self.config.timeout_navigation)
                await self.page.fill("input[name='q']", query)
                await self.page.press("input[name='q']", "Enter")

            await self.page.wait_for_load_state("domcontentloaded")
            return f"Buscando '{query}' en {engine}..."

        except Exception as e:
            return f"Error en busqueda: {e}"

    async def _click(self, selector: str) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None

            if " " in selector or not any(c in selector for c in ".#[]>:"):
                await self.page.get_by_text(selector).first.click()
            else:
                await self.page.click(selector)
            return f"Click en '{selector}'"
        except Exception as e:
            return f"No pude hacer click en '{selector}': {e}"

    async def _click_element(self, element_type: str, provider: str) -> str:
        try:
            await self._ensure_browser()
            element = await self._find_element(provider, element_type, timeout=10.0)
            await element.click()
            return f"Click en {element_type}"
        except Exception as e:
            return f"No pude hacer click en {element_type}: {e}"

    async def _type_text(self, selector: str, text: str) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None

            if selector in ["active", "focus", "here"]:
                await self.page.keyboard.type(text)
                return f"Escrito '{text}' en elemento activo"

            await self.page.fill(selector, text)
            return f"Escrito '{text}' en '{selector}'"
        except Exception as e:
            return f"Error escribiendo: {e}"

    async def _scroll(self, direction: str, amount: int) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None
            delta_y = amount if direction == "down" else -amount
            await self.page.mouse.wheel(0, delta_y)
            return f"Scroll {direction}"
        except Exception as e:
            return f"Error haciendo scroll: {e}"

    async def _get_text(self, selector: Optional[str]) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None
            if selector:
                text = await self.page.inner_text(selector)
            else:
                text = await self.page.inner_text("body")
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                text = '\n'.join(lines[:50])

            return f"Texto extraido:\n\n{text[:500]}..."
        except Exception as e:
            return f"Error leyendo texto: {e}"

    async def _screenshot(self) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None
            timestamp = int(time.time())
            filename = f"screenshot_{timestamp}.png"
            path = self.screenshot_dir / filename
            await self.page.screenshot(path=str(path))
            playwright_logger.info(f"Screenshot: {filename}")
            return f"Captura guardada: {filename}"
        except Exception as e:
            playwright_logger.error(f"Error screenshot: {e}")
            return f"Error tomando captura: {e}"

    async def _close(self) -> str:
        try:
            try:
                await self._stop_command_queue()
            except Exception:
                pass
            try:
                await self._stop_health_monitor()
            except Exception:
                pass

            await self._close_browser_internal()
            await self._set_state(BrowserState.CLOSED, "user_close")
            playwright_logger.info("Navegador cerrado")
            self._cleanup_old_screenshots()
            return "Navegador cerrado"
        except Exception as e:
            playwright_logger.error(f"Error cerrando: {e}")
            return f"Error cerrando: {e}"

    async def _play_media(self, query: str, provider: str) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None
            start_time = time.time()

            if provider not in ("youtube", "youtube_music"):
                return f"Proveedor no soportado: {provider}"

            # Strategy: go directly to the results URL instead of filling the
            # search box. This bypasses SPA reflows, cookie-consent races, and
            # selector breakage on the home page. It's also idempotent across
            # consecutive plays — no matter what state YouTube is in, /results?
            # always renders a video list.
            from urllib.parse import quote_plus
            q = quote_plus(query.strip())
            if provider == "youtube":
                results_url = f"https://www.youtube.com/results?search_query={q}"
            else:  # youtube_music
                results_url = f"https://music.youtube.com/search?q={q}"

            await self.page.goto(results_url, timeout=self.config.timeout_navigation)
            await self.page.wait_for_load_state("domcontentloaded")
            await self._handle_cookie_popups()

            # Prefer a real <a> link that points at /watch — that is the element
            # YouTube actually treats as the "go to video" click target. Parent
            # <ytd-video-renderer> wrappers open hover cards instead of navigating.
            if provider == "youtube":
                clicked = False
                link = self.page.locator("a#video-title-link, a#video-title, ytd-video-renderer a[href*='/watch']").first
                try:
                    await link.wait_for(state="visible", timeout=10000)
                    await link.scroll_into_view_if_needed(timeout=3000)
                    # Read href first — fall back to direct goto if click misfires.
                    href = await link.get_attribute("href")
                    try:
                        await link.click(timeout=5000)
                        clicked = True
                    except Exception as click_err:
                        playwright_logger.warning(f"PLAY_MEDIA link.click failed: {click_err}")

                    # Confirm we reached a /watch page; otherwise force navigation.
                    try:
                        await self.page.wait_for_url("**/watch*", timeout=8000)
                    except Exception:
                        if href:
                            watch_url = href if href.startswith("http") else f"https://www.youtube.com{href}"
                            try:
                                await self.page.goto(watch_url, timeout=self.config.timeout_navigation)
                                clicked = True
                            except Exception as goto_err:
                                playwright_logger.warning(f"PLAY_MEDIA goto watch fallback failed: {goto_err}")
                except Exception as link_err:
                    playwright_logger.warning(f"PLAY_MEDIA video link locator missed: {link_err}")

                if not clicked:
                    # Last-resort: legacy selector-list fallback.
                    try:
                        first_item = await self._find_element("youtube", "video_item")
                        await first_item.click()
                    except Exception:
                        pass
            else:
                # YouTube Music: aplicar el chip "Canciones" antes del click para
                # preferir versiones de estudio sobre live/cover/remix. YT Music
                # ordena por relevancia algorítmica y el primer resultado suele
                # ser un live u otra variante. El chip filtra a solo "Canciones".
                try:
                    chip = self.page.locator(
                        "ytmusic-chip-cloud-chip-renderer:has-text('Canciones'), "
                        "ytmusic-chip-cloud-chip-renderer:has-text('Songs'), "
                        "yt-chip-cloud-chip-renderer:has-text('Canciones'), "
                        "yt-chip-cloud-chip-renderer:has-text('Songs')"
                    ).first
                    if await chip.is_visible(timeout=2500):
                        await chip.click(timeout=2000)
                        # Esperar a que se aplique el filtro y se actualicen los resultados
                        await self.page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass  # fallback silencioso: continuar sin filtro

                # Click el enlace /watch del primer resultado de canción.
                # El contenedor ytmusic-responsive-list-item-renderer NO navega al clicar —
                # hay que apuntar al <a> interno con href que contenga /watch.
                clicked_yt_music = False
                link_selectors = [
                    "ytmusic-responsive-list-item-renderer a.yt-simple-endpoint[href*='watch']",
                    "ytmusic-shelf-renderer a.yt-simple-endpoint[href*='watch']",
                    "ytmusic-card-shelf-renderer a[href*='watch']",
                    "a.yt-simple-endpoint[href*='watch']",
                ]
                for sel in link_selectors:
                    try:
                        link = self.page.locator(sel).first
                        await link.wait_for(state="visible", timeout=5000)
                        href = await link.get_attribute("href")
                        try:
                            await link.click(timeout=5000)
                            clicked_yt_music = True
                        except Exception:
                            pass
                        # Si el click no navegó, forzar goto
                        try:
                            await self.page.wait_for_url("**/watch*", timeout=6000)
                        except Exception:
                            if href and not clicked_yt_music:
                                watch_url = href if href.startswith("http") else f"https://music.youtube.com{href}"
                                await self.page.goto(watch_url, timeout=self.config.timeout_navigation)
                                clicked_yt_music = True
                        break
                    except Exception:
                        continue
                if not clicked_yt_music:
                    playwright_logger.warning("PLAY_MEDIA youtube_music: no se encontró enlace /watch en resultados")

            # Give the player a moment to attach, then force-play if needed.
            try:
                await self.page.wait_for_selector("video.html5-main-video, video.video-stream, video", timeout=10000)
                is_paused = await self.page.evaluate(
                    "() => { const v = document.querySelector('video'); return v ? v.paused : true; }"
                )
                if is_paused:
                    # Prefer pressing 'k' which is YouTube's play/pause keybind
                    # and counts as a user gesture.
                    try:
                        await self.page.keyboard.press("k")
                    except Exception:
                        pass
                    # If still paused, try the big play button.
                    try:
                        is_paused = await self.page.evaluate(
                            "() => { const v = document.querySelector('video'); return v ? v.paused : true; }"
                        )
                    except Exception:
                        is_paused = False
                    if is_paused:
                        try:
                            play_button = await self._find_element(provider, "play_button", timeout=3.0)
                            await play_button.click()
                        except Exception:
                            pass
            except Exception:
                pass  # Best-effort; if we can't confirm, we already clicked.

            duration = time.time() - start_time
            playwright_logger.info(f"PLAY_MEDIA ok | provider={provider} | {duration:.1f}s")
            self._log_music_play("song", query)
            return f"Reproduciendo '{query}' en {provider}"
        except Exception as e:
            await self._take_debug_screenshot(f"play_{provider}")
            playwright_logger.error(f"PLAY_MEDIA fail | provider={provider} | {e}")
            return f"Error reproduciendo: {e}"

    # =========================================================================
    # YouTube Music extendido: artistas, álbumes, recomendaciones
    # =========================================================================

    def _log_music_play(self, kind: str, name: str) -> None:
        """Registra el play en UserProfile.interests para alimentar el prompt."""
        if not self.user_profile or not name:
            return
        try:
            topic = f"música:{kind}:{name.strip().lower()}"[:120]
            self.user_profile.add_interest(topic)
        except Exception as e:
            debug_log(f"_log_music_play silenced: {e}")

    async def _click_first_visible(self, selectors: list, timeout_ms: int = 6000, wait_state: str = "visible") -> Optional[Any]:
        """Helper: intenta cada selector y clica el primer elemento. wait_state puede ser 'visible' o 'attached'."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                await loc.wait_for(state=wait_state, timeout=timeout_ms)
                try:
                    await loc.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                await loc.click(timeout=4000)
                return loc
            except Exception:
                continue
        return None

    async def _click_yt_music_play_main(self) -> bool:
        """
        Click el botón grande Reproducir de la página de artista/álbum.
        Usa JavaScript .click() porque los custom elements de YT Music no siempre
        responden al click sintético de Playwright.
        """
        # Estrategia 1: JS click en el botón de reproducción del header del álbum/artista
        try:
            clicked = await self.page.evaluate("""
                () => {
                    const candidates = [
                        'ytmusic-detail-header-renderer ytmusic-play-button-renderer',
                        'ytmusic-immersive-header-renderer ytmusic-play-button-renderer',
                        'ytmusic-play-button-renderer[aria-label="Reproducir"]',
                        'ytmusic-play-button-renderer[aria-label="Play"]',
                        'ytmusic-play-button-renderer',
                        'button[aria-label="Reproducir"]:not([aria-label*="leatori"])',
                        'button[aria-label="Play"]:not([aria-label*="huffle"])',
                    ];
                    for (const sel of candidates) {
                        const el = document.querySelector(sel);
                        if (el) { el.click(); return sel; }
                    }
                    return null;
                }
            """)
            if clicked:
                playwright_logger.info(f"PLAY_MAIN: clicked via JS on '{clicked}'")
                await asyncio.sleep(1.0)
                return True
        except Exception as e:
            playwright_logger.debug(f"PLAY_MAIN: JS click failed: {e}")

        # Estrategia 2: Playwright click con los selectores CSS
        selectors = self.selectors.get("youtube_music", {}).get("play_btn_main", [])
        return (await self._click_first_visible(selectors, timeout_ms=6000)) is not None

    async def _click_yt_music_shuffle(self) -> bool:
        selectors = self.selectors.get("youtube_music", {}).get("shuffle_btn", [])
        return (await self._click_first_visible(selectors, timeout_ms=6000)) is not None

    async def _wait_video_playing(self, timeout_ms: int = 12000) -> bool:
        """Espera a que <video> esté presente y con datos. Fuerza play si está pausado."""
        try:
            await self.page.wait_for_selector("video", timeout=timeout_ms)
        except Exception:
            return False
        try:
            state = await self.page.evaluate(
                "() => { const v=document.querySelector('video'); return v ? {paused:v.paused, ready:v.readyState} : null; }"
            )
        except Exception:
            return False
        if not state:
            return False
        if state.get("paused", True):
            try:
                await self.page.keyboard.press("k")
            except Exception:
                pass
        return True

    async def _goto_artist_page(self, name: str) -> bool:
        """
        Busca un artista y navega a su página de canal (channel/UC...).

        Devuelve True si llegó a la página del artista, False si no encontró la
        tarjeta. NO usa el chip "Artistas": con el nombre del artista la página
        ya pone su canal como Top result; si el click falla, navega al href.
        """
        assert self.page is not None
        from urllib.parse import quote_plus

        results_url = f"https://music.youtube.com/search?q={quote_plus(name.strip())}"
        await self.page.goto(results_url, timeout=self.config.timeout_navigation)
        await self.page.wait_for_load_state("domcontentloaded")
        await self._handle_cookie_popups()
        try:
            await self.page.wait_for_selector(
                "ytmusic-tabbed-search-results-renderer, ytmusic-shelf-renderer, ytmusic-card-shelf-renderer",
                timeout=10000,
            )
        except Exception:
            pass
        await asyncio.sleep(1.5)

        card_selectors = self.selectors.get("youtube_music", {}).get("artist_card", [])
        clicked = None
        channel_href = None
        for sel in card_selectors:
            try:
                loc = self.page.locator(sel).first
                await loc.wait_for(state="attached", timeout=8000)
                channel_href = await loc.get_attribute("href")
                try:
                    await loc.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    await loc.click(timeout=4000)
                    clicked = loc
                    break
                except Exception:
                    if channel_href:
                        full = channel_href if channel_href.startswith("http") else f"https://music.youtube.com/{channel_href.lstrip('/')}"
                        await self.page.goto(full, timeout=self.config.timeout_navigation)
                        clicked = loc
                        break
            except Exception:
                continue
        if not clicked:
            return False

        try:
            await self.page.wait_for_url("**channel/UC**", timeout=8000)
        except Exception:
            pass
        await self._handle_cookie_popups()
        return True

    async def _play_yt_music_artist(self, name: str, mode: str = "shuffle") -> str:
        """Busca un artista, abre su página y lanza play o shuffle."""
        try:
            await self._ensure_browser()
            assert self.page is not None
            t0 = time.time()

            if not await self._goto_artist_page(name):
                playwright_logger.warning(f"PLAY_ARTIST: no se encontró tarjeta de artista para '{name}'")
                return f"No encontré la página del artista {name}"

            # Lanzar reproducción
            if mode == "play":
                ok = await self._click_yt_music_play_main() or await self._click_yt_music_shuffle()
            else:
                ok = await self._click_yt_music_shuffle() or await self._click_yt_music_play_main()
            if not ok:
                playwright_logger.warning(f"PLAY_ARTIST: no encontré botón Reproducir/Aleatorio para '{name}'")

            await self._wait_video_playing()
            duration = time.time() - t0
            playwright_logger.info(f"PLAY_ARTIST ok | name={name!r} | mode={mode} | {duration:.1f}s")
            self._log_music_play("artist", name)
            return f"Reproduciendo música de {name} ({mode}) en YouTube Music"
        except Exception as e:
            await self._take_debug_screenshot("play_artist")
            playwright_logger.error(f"PLAY_ARTIST fail | name={name!r} | {type(e).__name__}: {e!r}")
            return f"Error reproduciendo artista: {e}"

    async def _start_album_playback(self) -> bool:
        """
        Asume que la page está en la página de un álbum (browse/MPRE o playlist)
        e inicia la reproducción del primer track. Devuelve True si arrancó.
        """
        ok = await self._click_yt_music_play_main()
        # Si el botón no inició la reproducción, navegar directo al primer track.
        if not ok or "/watch" not in self.page.url:
            await asyncio.sleep(1.0)
            # Fallback A: primer /watch link del track list
            first_watch = await self.page.evaluate("""
                () => {
                    const link = document.querySelector(
                        'ytmusic-playlist-shelf-renderer a[href*="watch"], ' +
                        'ytmusic-responsive-list-item-renderer a[href*="watch"]'
                    );
                    return link ? link.href : null;
                }
            """)
            if first_watch:
                playwright_logger.info(f"PLAY_ALBUM: navegando al primer track {first_watch}")
                await self.page.goto(first_watch, timeout=self.config.timeout_navigation)
                ok = True
            else:
                # Fallback B: click de la primera pista vía Playwright
                playwright_logger.warning("PLAY_ALBUM: no hallé /watch en DOM, probando click primera pista")
                track_selectors = self.selectors.get("youtube_music", {}).get("first_track_in_list", [])
                ok = (await self._click_first_visible(track_selectors, timeout_ms=8000, wait_state="attached")) is not None

        # Esperar a que YT Music empiece a reproducir un track
        try:
            await self.page.wait_for_url("**/watch*", timeout=8000)
        except Exception:
            pass
        return ok

    async def _play_yt_music_album(self, query: str, match_title: Optional[str] = None) -> str:
        """
        Busca, filtra por Álbumes y reproduce un álbum.

        Si `match_title` está presente, elige la tarjeta de álbum cuyo título
        COINCIDE con ese nombre (en vez de clickear la primera, que reproducía
        el álbum equivocado cuando el filtro de YouTube Music rankeaba mal). Si
        no hay match, devuelve un fallo honesto en vez de reproducir cualquier cosa.
        """
        try:
            await self._ensure_browser()
            assert self.page is not None
            from urllib.parse import quote_plus
            t0 = time.time()
            target_label = match_title or query

            results_url = f"https://music.youtube.com/search?q={quote_plus(query.strip())}"
            await self.page.goto(results_url, timeout=self.config.timeout_navigation)
            await self.page.wait_for_load_state("domcontentloaded")
            await self._handle_cookie_popups()
            try:
                await self.page.wait_for_selector(
                    "ytmusic-shelf-renderer, ytmusic-card-shelf-renderer, ytmusic-tabbed-search-results-renderer",
                    timeout=10000,
                )
            except Exception:
                pass
            await asyncio.sleep(1.0)

            chip_selectors = self.selectors.get("youtube_music", {}).get("filter_chip_albums", [])
            chip_clicked = await self._click_first_visible(chip_selectors, timeout_ms=4000)
            if chip_clicked:
                await asyncio.sleep(1.0)

            if match_title:
                # Match por NOMBRE entre las tarjetas de álbum de los resultados.
                href = await self.page.evaluate(_ALBUM_MATCH_JS, match_title)
                if not href:
                    playwright_logger.warning(f"PLAY_ALBUM: '{match_title}' no está en los resultados de álbumes")
                    return f"No encontré el álbum {match_title}"
                full = href if href.startswith("http") else f"https://music.youtube.com/{href.lstrip('/')}"
                await self.page.goto(full, timeout=self.config.timeout_navigation)
            else:
                card_selectors = self.selectors.get("youtube_music", {}).get("album_card", [])
                clicked = await self._click_first_visible(card_selectors, timeout_ms=10000, wait_state="attached")
                if not clicked:
                    playwright_logger.warning(f"PLAY_ALBUM: no se encontró álbum para '{query}'")
                    return f"No encontré el álbum {query}"
                # Esperar navegación a la página del álbum
                try:
                    await self.page.wait_for_url("**/playlist?list=OLAK5uy_*", timeout=10000)
                except Exception:
                    try:
                        await self.page.wait_for_url("**/browse/MPRE*", timeout=5000)
                    except Exception:
                        pass

            await self._handle_cookie_popups()
            # Dejar que el DOM del álbum renderice antes de buscar el botón play
            await asyncio.sleep(2.0)

            ok = await self._start_album_playback()
            if not ok:
                playwright_logger.warning(f"PLAY_ALBUM: no encontro ninguna forma de reproducir '{target_label}'")

            await self._wait_video_playing()
            duration = time.time() - t0
            playwright_logger.info(
                f"PLAY_ALBUM ok | query={query!r} match={match_title!r} | url={self.page.url} | {duration:.1f}s"
            )
            self._log_music_play("album", target_label)
            return f"Reproduciendo álbum '{target_label}' en YouTube Music"
        except Exception as e:
            await self._take_debug_screenshot("play_album")
            playwright_logger.error(f"PLAY_ALBUM fail | query={query!r} | {type(e).__name__}: {e!r}")
            return f"Error reproduciendo álbum: {e}"

    async def _play_best_album_for_artist(self, artist: str) -> str:
        """
        Reproduce el álbum más popular de un artista en YT Music.

        Estrategia: en la página del artista, la shelf "Canciones más populares"
        ordena tracks por reproducciones. El primer track tiene un `list=OLAK5uy_*`
        que apunta al ÁLBUM al que pertenece esa canción top — por construcción,
        ese álbum es el más popular del artista (contiene el hit más reproducido).

        OJO: la shelf "Álbumes" ordena por fecha de lanzamiento (más reciente
        primero), NO por popularidad — por eso no usamos esa shelf.
        """
        try:
            await self._ensure_browser()
            assert self.page is not None
            t0 = time.time()

            # 1) Ir a la página del artista
            if not await self._goto_artist_page(artist):
                return f"No encontré la página del artista {artist}"
            await asyncio.sleep(1.5)

            # 2) Extraer el list= del primer track de la shelf "Canciones más populares".
            # Ese list ID apunta al álbum que contiene la canción más reproducida del
            # artista — por definición, su álbum más popular.
            best = await self.page.evaluate("""
                () => {
                    function normalize(s) {
                        // colapsar whitespace y quitar diacríticos
                        return (s||'').replace(/\\s+/g, ' ').trim().toLowerCase()
                            .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
                    }
                    const shelves = document.querySelectorAll(
                        'ytmusic-shelf-renderer, ytmusic-carousel-shelf-renderer'
                    );
                    // Headers que indican la shelf de "top tracks" del artista.
                    // Tras normalize: tildes y espacios extra eliminados.
                    const TOP_PATTERNS = [
                        /^canciones mas populares\\b/,
                        /^top songs\\b/,
                        /^popular songs\\b/,
                        /^songs\\b/,
                    ];
                    let topShelf = null;
                    let matchedHeader = '';
                    for (const shelf of shelves) {
                        const rawHeader = shelf.querySelector('h2')?.textContent || '';
                        const h = normalize(rawHeader);
                        if (TOP_PATTERNS.some(re => re.test(h))) {
                            topShelf = shelf;
                            matchedHeader = h;
                            break;
                        }
                    }
                    if (!topShelf) {
                        // Para diagnóstico: lista los headers que sí vimos
                        const seen = [];
                        shelves.forEach(s => seen.push(normalize(s.querySelector('h2')?.textContent || '').slice(0, 50)));
                        return { error: 'no_top_shelf', seenHeaders: seen };
                    }
                    const firstWatch = topShelf.querySelector('a.yt-simple-endpoint[href*="watch?v="][href*="list=OLAK5uy_"]');
                    if (!firstWatch) return { error: 'no_watch_link', matchedHeader };
                    const href = firstWatch.getAttribute('href');
                    const m = href.match(/list=(OLAK5uy_[^&]+)/);
                    if (!m) return { error: 'no_list_param', href };
                    // título del track: para list-item el título está en el primer .title o en el aria-label del link
                    const itemRoot = firstWatch.closest('ytmusic-responsive-list-item-renderer') || topShelf;
                    const trackTitle = (itemRoot.querySelector('.title') || itemRoot.querySelector('[title]'))?.textContent?.trim() || '';
                    return { listId: m[1], trackHref: firstWatch.href, trackTitle, matchedHeader };
                }
            """)
            if best and best.get("error"):
                playwright_logger.warning(f"BEST_ALBUM: detección de shelf falló | {best}")
                best = None

            if not best:
                playwright_logger.warning(f"BEST_ALBUM: no halló shelf 'Canciones más populares', fallback a search '{artist} álbum'")
                return await self._play_yt_music_album(f"{artist} álbum")

            list_id = best["listId"]
            playwright_logger.info(f"BEST_ALBUM: top track={best.get('trackTitle')!r} -> album list={list_id}")

            # Navegar al watch URL — esto inicia reproducción inmediata del álbum
            watch_url = f"https://music.youtube.com/watch?list={list_id}"
            await self.page.goto(watch_url, timeout=self.config.timeout_navigation)
            await self.page.wait_for_load_state("domcontentloaded")
            await self._handle_cookie_popups()
            await asyncio.sleep(2.0)

            # 3) Confirmar reproducción y guardar metadatos del álbum elegido
            try:
                await self.page.wait_for_url("**/watch*", timeout=8000)
            except Exception:
                pass

            await self._wait_video_playing()
            # Capturar el título del álbum que efectivamente está sonando para logging y verificación
            album_title = ""
            try:
                album_title = await self.page.evaluate("""
                    () => {
                        const t = document.querySelector('ytmusic-player-bar .byline a, ytmusic-player-bar yt-formatted-string.byline');
                        return t ? t.textContent.trim() : '';
                    }
                """)
            except Exception:
                pass

            duration = time.time() - t0
            playwright_logger.info(
                f"BEST_ALBUM ok | artist={artist!r} | list={list_id} | "
                f"album_byline={album_title!r} | url={self.page.url} | {duration:.1f}s"
            )
            self._log_music_play("album_best", artist)
            return f"Reproduciendo el álbum más popular de {artist} en YouTube Music"
        except Exception as e:
            await self._take_debug_screenshot("play_best_album")
            playwright_logger.error(f"BEST_ALBUM fail | artist={artist!r} | {type(e).__name__}: {e!r}")
            return f"Error buscando mejor álbum de {artist}: {e}"

    async def _play_named_album_for_artist(
        self, artist: str, album: str, fallback_query: Optional[str] = None
    ) -> str:
        """
        Reproduce un álbum específico (por nombre) de un artista. Cascada:

          Tier 1: página del artista → álbum por nombre en su discografía
                  (lo más confiable: matcheamos dentro del catálogo de UN artista).
          Tier 2: búsqueda + filtro Álbumes + match por nombre de la tarjeta.

        Si nada coincide, devuelve un fallo honesto — NUNCA reproduce un álbum
        equivocado (que era el bug original del "clic en la primera tarjeta").
        """
        try:
            await self._ensure_browser()
            assert self.page is not None
            t0 = time.time()

            # Tier 1: discografía del artista
            if await self._goto_artist_page(artist):
                await asyncio.sleep(1.5)
                try:
                    href = await self.page.evaluate(_ALBUM_MATCH_JS, album)
                except Exception:
                    href = None
                if href:
                    full = href if href.startswith("http") else f"https://music.youtube.com/{href.lstrip('/')}"
                    await self.page.goto(full, timeout=self.config.timeout_navigation)
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self._handle_cookie_popups()
                    await asyncio.sleep(2.0)
                    ok = await self._start_album_playback()
                    if not ok:
                        playwright_logger.warning(
                            f"NAMED_ALBUM: hallé el álbum pero no pude reproducirlo | "
                            f"album={album!r} artist={artist!r}"
                        )
                    await self._wait_video_playing()
                    duration = time.time() - t0
                    playwright_logger.info(
                        f"NAMED_ALBUM ok (artist page) | album={album!r} artist={artist!r} | "
                        f"url={self.page.url} | {duration:.1f}s"
                    )
                    self._log_music_play("album_named", f"{album} - {artist}")
                    return f"Reproduciendo el álbum '{album}' de {artist} en YouTube Music"
                playwright_logger.info(
                    f"NAMED_ALBUM: '{album}' no está en la discografía visible de {artist}; "
                    f"fallback a búsqueda"
                )
            else:
                playwright_logger.info(
                    f"NAMED_ALBUM: no encontré la página del artista {artist}; fallback a búsqueda"
                )

            # Tier 2: búsqueda con match por nombre (no clic a ciegas)
            return await self._play_yt_music_album(
                fallback_query or f"{album} {artist}", match_title=album
            )
        except Exception as e:
            await self._take_debug_screenshot("play_named_album")
            playwright_logger.error(
                f"NAMED_ALBUM fail | album={album!r} artist={artist!r} | {type(e).__name__}: {e!r}"
            )
            return f"Error reproduciendo el álbum '{album}' de {artist}: {e}"

    async def _play_yt_music_recommendations(self, seed: Optional[str] = None) -> str:
        """
        Sin seed: home de YT Music → primera mezcla personalizada.
        Con seed: equivalente a shuffle del artista (radio).
        """
        if seed:
            return await self._play_yt_music_artist(seed, mode="shuffle")
        try:
            await self._ensure_browser()
            assert self.page is not None
            t0 = time.time()

            await self.page.goto("https://music.youtube.com/", timeout=self.config.timeout_navigation)
            await self.page.wait_for_load_state("domcontentloaded")
            await self._handle_cookie_popups()
            try:
                await self.page.wait_for_selector("ytmusic-carousel-shelf-renderer", timeout=10000)
            except Exception:
                pass

            mix_selectors = self.selectors.get("youtube_music", {}).get("home_first_mix_card", [])
            clicked = await self._click_first_visible(mix_selectors, timeout_ms=8000)
            if not clicked:
                playwright_logger.warning("PLAY_REC: no encontré ninguna mezcla en la home")
                return "No encontré recomendaciones en YouTube Music"

            try:
                await self.page.wait_for_url("**/watch*", timeout=8000)
            except Exception:
                # Si abrió una página de playlist intermedia, dale play
                await self._click_yt_music_play_main()

            await self._wait_video_playing()
            duration = time.time() - t0
            playwright_logger.info(f"PLAY_REC ok | seed=None | {duration:.1f}s")
            self._log_music_play("mix", "yt_music_home")
            return "Reproduciendo recomendaciones de YouTube Music"
        except Exception as e:
            await self._take_debug_screenshot("play_recommendations")
            playwright_logger.error(f"PLAY_REC fail | {e}")
            return f"Error en recomendaciones: {e}"

    # =========================================================================
    # Controles de reproducción en tiempo real (pause/next/prev/mute/stop)
    # Operan directo sobre <video> o botones del player bar vía JS.
    # No llaman _ensure_browser() — si no hay page, retornan inmediatamente.
    # =========================================================================

    async def _set_playback(self, paused: bool) -> str:
        """
        Lleva la reproducción al estado deseado de forma DETERMINÍSTICA:
        pausar siempre pausa, reanudar siempre reproduce (no es un toggle, así
        decir "pausa" dos veces no reanuda por accidente).

        Devuelve un mensaje claro del estado resultante ("Música pausada" /
        "Reproduciendo música") en vez del ambiguo "Reproducción alternada".
        """
        if not self.page:
            return "No hay reproducción activa"
        try:
            result = await self.page.evaluate(
                """
                (wantPaused) => {
                    const v = document.querySelector('video');
                    if (!v) return 'no_video';
                    if (wantPaused) {
                        if (!v.paused) v.pause();
                    } else {
                        if (v.paused) { const p = v.play(); if (p && p.catch) p.catch(() => {}); }
                    }
                    return v.paused ? 'paused' : 'playing';
                }
                """,
                paused,
            )
            if result == 'no_video':
                # Fallback: atajo de teclado de YouTube/YouTube Music (toggle).
                await self.page.keyboard.press('k')
            # v.play() es asíncrono: la lectura inmediata puede seguir en 'paused'.
            # Reportamos según la intención, ya confirmado que hay reproductor.
            return "Música pausada" if paused else "Reproduciendo música"
        except Exception as e:
            return f"Error al controlar la reproducción: {type(e).__name__}: {e}"

    async def _pause_media(self) -> str:
        return await self._set_playback(paused=True)

    async def _resume_media(self) -> str:
        return await self._set_playback(paused=False)

    async def _next_track(self) -> str:
        if not self.page:
            return "No hay reproducción activa"
        try:
            clicked = await self.page.evaluate("""
                () => {
                    const SELS = [
                        'ytmusic-player-bar .next-button',
                        'ytmusic-player-bar tp-yt-paper-icon-button[aria-label="Next song"]',
                        'ytmusic-player-bar tp-yt-paper-icon-button[aria-label="Siguiente cancion"]',
                        'ytmusic-player-bar tp-yt-paper-icon-button[aria-label="Siguiente canción"]',
                        '#next-button',
                    ];
                    for (const sel of SELS) {
                        const btn = document.querySelector(sel);
                        if (btn) { btn.click(); return true; }
                    }
                    return false;
                }
            """)
            return "Siguiente canción" if clicked else "No se encontró el botón de siguiente"
        except Exception as e:
            return f"Error cambiando de canción: {e}"

    async def _prev_track(self) -> str:
        if not self.page:
            return "No hay reproducción activa"
        try:
            clicked = await self.page.evaluate("""
                () => {
                    const SELS = [
                        'ytmusic-player-bar .previous-button',
                        'ytmusic-player-bar tp-yt-paper-icon-button[aria-label="Previous song"]',
                        'ytmusic-player-bar tp-yt-paper-icon-button[aria-label="Cancion anterior"]',
                        'ytmusic-player-bar tp-yt-paper-icon-button[aria-label="Canción anterior"]',
                        '#previous-button',
                    ];
                    for (const sel of SELS) {
                        const btn = document.querySelector(sel);
                        if (btn) { btn.click(); return true; }
                    }
                    return false;
                }
            """)
            return "Canción anterior" if clicked else "No se encontró el botón de anterior"
        except Exception as e:
            return f"Error cambiando de canción: {e}"

    async def _toggle_mute(self) -> str:
        if not self.page:
            return "No hay reproducción activa"
        try:
            result = await self.page.evaluate("""
                () => {
                    const v = document.querySelector('video');
                    if (!v) return 'no_video';
                    v.muted = !v.muted;
                    return v.muted ? 'muted' : 'unmuted';
                }
            """)
            if result == 'no_video':
                return "No hay video reproduciéndose"
            return "Audio silenciado" if result == 'muted' else "Audio activado"
        except Exception as e:
            return f"Error al silenciar: {e}"

    async def _stop_music(self) -> str:
        if not self.page:
            return "No hay reproducción activa"
        try:
            await self.page.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")
            await self.page.goto("about:blank", timeout=5000)
        except Exception:
            pass
        return "Reproducción cerrada"

    async def _control_media(self, action: str) -> str:
        try:
            await self._ensure_browser()
            assert self.page is not None

            action = action.lower()
            if action in ["pausa", "pause", "play", "reproduce"]:
                await self.page.keyboard.press("k")
                state = "pausado" if action in ["pausa", "pause"] else "reproduciendo"
                return f"{state.capitalize()}"
            elif action in ["volumen arriba", "volume up"]:
                await self.page.keyboard.press("ArrowUp")
                return "Volumen aumentado"
            elif action in ["volumen abajo", "volume down"]:
                await self.page.keyboard.press("ArrowDown")
                return "Volumen disminuido"
            elif action in ["pantalla completa", "fullscreen"]:
                await self.page.keyboard.press("f")
                return "Pantalla completa"
            else:
                return f"Accion no reconocida: {action}"
        except Exception as e:
            playwright_logger.error(f"CONTROL_MEDIA fail | {action} | {e}")
            return f"Error controlando medios: {e}"

    async def _wait_for(self, element_type: str, provider: str, timeout: float) -> str:
        try:
            await self._ensure_browser()
            await self._find_element(provider, element_type, timeout)
            playwright_logger.info(f"WAIT_FOR ok | {provider}.{element_type}")
            return f"Elemento {element_type} encontrado"
        except Exception as e:
            playwright_logger.error(f"WAIT_FOR fail | {provider}.{element_type} | {e}")
            return f"No se encontro {element_type} despues de {timeout}s"

    async def _find_by_text(self, text: str, partial: bool) -> str:
        """Busca un elemento por su texto visible y devuelve su posición.

        Estaba roto: hacía `await self.page.get_by_text(...).first`, y
        `get_by_text()` devuelve un `Locator` sincrónico — `await` sobre un
        Locator lanza `TypeError: object Locator can't be used in 'await'
        expression`, siempre. El `except` lo convertía en un string de error,
        así que fallaba CERRADO y nadie se enteró.

        Importa porque `find_by_text` es el fallback genérico de INTERACT en
        `planner.py`, y `action_risk_rules._CLICK_ACTIONS` lo lista como si
        clickeara. O sea: toda interacción genérica por texto estuvo muerta.
        """
        try:
            await self._ensure_browser()
            assert self.page is not None
            locator = self.page.get_by_text(text, exact=not partial).first
            # El Locator es perezoso: recién al pedir la bounding box se
            # resuelve, y devuelve None si el elemento no está en el layout.
            bbox = await locator.bounding_box(timeout=5000)
            if bbox:
                return f"Elemento con texto '{text[:50]}' encontrado en {bbox}"
            return f"No se encontro elemento con texto '{text[:50]}'"
        except Exception as e:
            return f"Error buscando por texto: {e}"

    async def _close_tab(self) -> str:
        # Guard: si no hay browser abierto, no hay nada que cerrar
        if self._state == BrowserState.CLOSED:
            playwright_logger.info("CLOSE_TAB | no browser open, skipping")
            return "No hay navegador abierto"
        try:
            await self._ensure_browser()
            assert self.context is not None
            assert self.page is not None
            # Solo se cierra NUESTRA pestaña. Si `self.page` quedó apuntando a
            # una ajena, se suelta y no se cierra nada: "cerrá la pestaña"
            # jamás debe llevarse una pestaña del usuario.
            if not self._owns_current_page():
                self._release_page_if_foreign()
                playwright_logger.info("CLOSE_TAB | foreign page, nothing to close")
                return "No tengo ninguna pestaña abierta para cerrar"

            pages = self.context.pages if self.context else []
            if len(pages) <= 1:
                await self._close()
                playwright_logger.info("CLOSE_TAB | last_tab")
                return "Ultima pestana cerrada (navegador cerrado)"
            else:
                _closing = self.page
                # Se SUELTA en vez de adoptar `pages[-2]`, que en modo CDP es
                # una pestaña del usuario (`self.context` es su contexto).
                self.page = None
                self._own_pages.discard(_closing)
                await _closing.close()
                playwright_logger.info("CLOSE_TAB | ok")
                return "Pestana cerrada"
        except Exception as e:
            playwright_logger.error(f"CLOSE_TAB fail | {e}")
            return f"Error cerrando pestana: {e}"

    # =========================================================================
    # METRICAS E INFO
    # =========================================================================

    def get_metrics(self) -> Dict[str, Any]:
        return self._metrics.copy()

    def get_state_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._state_history[-limit:] if self._state_history else []

    def get_session_info(self) -> Dict[str, Any]:
        queue_metrics = None
        if self._command_queue is not None:
            queue_metrics = self._command_queue.get_metrics()
        health_metrics = None
        if self._health_monitor is not None:
            health_metrics = self._health_monitor.get_metrics()
        return {
            "session_id": self._session_id,
            "state": self._state.value,
            "health": self._health.value,
            "headless": self.headless,
            "firefox_executable": self.config.firefox_executable,
            "user_profile": self.config.user_profile_path,
            "uptime": time.time() - self._start_time,
            "concurrent_operations": len(self._concurrent_operations),
            "metrics": self.get_metrics(),
            "queue_metrics": queue_metrics,
            "health_monitor_metrics": health_metrics,
        }

    def get_queue_status(self) -> Dict[str, Any]:
        if self._command_queue is None:
            return {"queue_initialized": False, "queue_size": 0, "worker_running": False}
        metrics = self._command_queue.get_metrics()
        return {
            "queue_initialized": True,
            "queue_size": self._command_queue.get_queue_size(),
            "worker_running": metrics.get("worker_running", False),
            "current_command": metrics.get("current_command"),
            "metrics": metrics,
        }

    # =========================================================================
    # CONTROL PUBLICO: kill / restart / tail_logs
    # =========================================================================

    async def kill(self) -> Dict[str, Any]:
        """
        Drena la cola, cierra browser y playwright, libera referencias.
        Devuelve {"success": bool, "message": str, "drained": int}.
        """
        drained = 0
        if self._command_queue is not None:
            drained = self._command_queue.get_queue_size()
            try:
                await asyncio.wait_for(self._stop_command_queue(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("kill(): command queue stop timeout")
                self._command_queue = None
        try:
            await self._close_browser_internal()
        except Exception as e:
            return {"success": False, "message": f"error cerrando browser: {e}", "drained": drained}
        await self._set_state(BrowserState.CLOSED, "killed")
        await self._set_health(BrowserHealth.UNKNOWN, "killed")
        logger.info(f"BrowserController.kill() OK (drained={drained})")
        return {"success": True, "message": "browser cerrado", "drained": drained}

    async def restart(self) -> Dict[str, Any]:
        """
        kill + relanzar browser. Devuelve {"success", "message", "session_id"}.
        """
        kill_result = await self.kill()
        if not kill_result.get("success"):
            return {"success": False, "message": kill_result.get("message", "kill failed")}
        try:
            await self._ensure_browser()
        except Exception as e:
            return {"success": False, "message": f"relanzamiento fallo: {e}"}
        return {
            "success": True,
            "message": "browser reiniciado",
            "session_id": self._session_id,
        }

    def tail_logs(self, n: int = 50) -> List[str]:
        """
        Lee las ultimas N lineas de playwright.log. Devuelve lista de strings.
        No bloquea — operacion sincronica corta sobre el archivo.
        """
        log_path = Path(self.config.log_file)
        if not log_path.is_absolute():
            # Resolver relativo al CWD del proceso (que es backend/)
            log_path = Path.cwd() / log_path
        if not log_path.exists():
            return []
        try:
            # Leer tail eficientemente: para archivos chicos, simplemente
            # leer todo y tomar las ultimas N lineas.
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return [ln.rstrip("\n") for ln in lines[-n:]]
        except Exception as e:
            logger.warning(f"tail_logs failed: {e}")
            return []


# Configurar logging basico
logging.basicConfig(level=logging.INFO)
