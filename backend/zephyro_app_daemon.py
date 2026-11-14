#!/usr/bin/env python3
"""
Zephyro App Daemon - Daemon gráfico para lanzamiento de aplicaciones
Corre en la sesión gráfica del usuario y mantiene contexto completo (DBus, XAUTHORITY)
"""
import os
import sys
import json
import shutil
import socket
import logging
import subprocess
import signal
import time
from pathlib import Path
from typing import Dict, Optional

# Add backend/ to sys.path so core.app_config is importable when running as daemon
_backend_dir = str(Path(__file__).parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from core.app_config import (
    APP_ALIASES, APP_COMMANDS, PROCESS_NAMES, WINDOW_PATTERNS as _CFG_WINDOW_PATTERNS,
    WEB_APP_URLS as _CFG_WEB_APP_URLS, resolve_alias,
)

# Configuración
def _default_socket_path() -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "zephyro-apps.sock")
    return "/tmp/zephyro-apps.sock"


SOCKET_PATH = os.environ.get("ZEPHYRO_DAEMON_SOCKET", _default_socket_path())
LOG_FILE = Path.home() / ".local/share/zephyro/daemon.log"

# Asegurar directorio de logs
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class AppDaemon:
    """Daemon que ejecuta aplicaciones con contexto gráfico completo"""
    
    WEB_APP_URLS = _CFG_WEB_APP_URLS
    
    # Patrones de título de ventana para verificación de éxito (from centralized config)
    WINDOW_PATTERNS = _CFG_WINDOW_PATTERNS
    
    def __init__(self):
        self.socket_path = SOCKET_PATH
        self.running = True
        self.is_wayland = os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
        
        # Capturar contexto gráfico
        self.graphical_env = {
            'DISPLAY': os.environ.get('DISPLAY'),
            'WAYLAND_DISPLAY': os.environ.get('WAYLAND_DISPLAY'),
            'XAUTHORITY': os.environ.get('XAUTHORITY'),
            'DBUS_SESSION_BUS_ADDRESS': os.environ.get('DBUS_SESSION_BUS_ADDRESS'),
            'XDG_RUNTIME_DIR': os.environ.get('XDG_RUNTIME_DIR'),
        }
        
        # Recoger procesos hijos zombie automáticamente
        def _reap_children(*_):
            try:
                while True:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                    if pid == 0:
                        break
            except ChildProcessError:
                pass
        signal.signal(signal.SIGCHLD, _reap_children)

        # Capturar señales para shutdown limpio
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        
        logger.info(f"Daemon iniciado - Entorno: {'Wayland' if self.is_wayland else 'X11'}")
        logger.info(f"DBus: {os.environ.get('DBUS_SESSION_BUS_ADDRESS', 'NO DISPONIBLE')}")
        logger.info(f"Display: {os.environ.get('DISPLAY', 'NO DISPONIBLE')}")
        logger.info(f"Wayland Display: {os.environ.get('WAYLAND_DISPLAY', 'NO DISPONIBLE')}")
    
    def _handle_signal(self, signum, frame):
        """Maneja señales de terminación"""
        logger.info(f"Señal {signum} recibida, cerrando daemon...")
        self.running = False
    
    def _is_app_running(self, process_name: str) -> bool:
        """Verifica si una aplicación está ejecutándose"""
        try:
            result = subprocess.run(
                ["pgrep", "-x", process_name],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def _candidate_process_names(self, app_name: str) -> list[str]:
        """Nombres de proceso candidatos para verificación robusta"""
        return PROCESS_NAMES.get(app_name, [app_name])
    
    def _check_window_exists(self, app_name: str, timeout: float = 3.0) -> bool:
        """Verifica si una ventana de la aplicación existe usando wmctrl"""
        patterns = self.WINDOW_PATTERNS.get(app_name, [])
        if not patterns:
            # Sin patrones definidos, asumir éxito
            return True
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ["wmctrl", "-l"],
                    capture_output=True,
                    text=True,
                    timeout=2.0
                )
                if result.returncode == 0:
                    window_list = result.stdout.lower()
                    # Buscar cualquier patrón en la lista de ventanas
                    for pattern in patterns:
                        if pattern.lower() in window_list:
                            logger.debug(f"Ventana encontrada para {app_name}: patrón '{pattern}'")
                            return True
            except (subprocess.SubprocessError, FileNotFoundError):
                # wmctrl no disponible o error
                logger.debug(f"wmctrl no disponible o error para {app_name}")
                return True  # Fallback a éxito si no podemos verificar
            
            time.sleep(0.3)
        
        logger.warning(f"No se encontró ventana para {app_name} después de {timeout}s")
        return False
    
    def _should_check_window(self, app_name: str) -> bool:
        """Determina si debemos verificar ventana para esta app"""
        return app_name in self.WINDOW_PATTERNS

    def _is_any_process_running(self, process_names: list[str]) -> bool:
        for name in process_names:
            if self._is_app_running(name):
                return True
        return False

    def _wait_for_process(self, process_names: list[str], timeout: float = 2.5, interval: float = 0.2) -> bool:
        """Espera a que algún proceso candidato aparezca"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_any_process_running(process_names):
                return True
            time.sleep(interval)
        return False
    
    def _get_env_for_electron(self) -> Dict[str, str]:
        """Retorna variables de entorno para apps Electron en Wayland"""
        env = os.environ.copy()
        if self.is_wayland:
            env['ELECTRON_OZONE_PLATFORM_HINT'] = 'x11'
        return env
    
    def _has_electron_renderer(self, app_name: str, timeout: float = 8.0) -> bool:
        """Verifica que una app Electron haya cargado su ventana principal.

        Las apps Electron (Discord, VSCode, etc.) lanzan procesos renderer
        cuando la ventana principal se abre. Los renderers aparecen como
        /proc/self/exe con --type=renderer y user-data-dir=.../<app>.
        El cmdline tiene --type=renderer ANTES de --user-data-dir, así que
        buscamos ambos términos por separado.
        """
        deadline = time.monotonic() + timeout
        app_lower = app_name.lower()
        while time.monotonic() < deadline:
            try:
                # Buscar procesos renderer y filtrar por nombre de app
                result = subprocess.run(
                    ["pgrep", "-af", "type=renderer"],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().splitlines():
                        if app_lower in line.lower():
                            return True
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
            time.sleep(0.5)
        return False

    def _launch_discord(self) -> Dict[str, any]:
        """Lanza Discord con configuración optimizada"""
        # Verificar si ya está corriendo
        if self._is_any_process_running(self._candidate_process_names("discord")):
            logger.info("Discord ya está ejecutándose")
            return {"success": True, "message": "Discord ya está abierto", "already_running": True}

        env = self._get_env_for_electron()
        discord_path = "/opt/discord/Discord"

        # Método principal: /opt/discord/Discord
        if os.path.exists(discord_path):
            try:
                cmd = [discord_path, "--no-sandbox", "--disable-gpu"]
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env
                )
                if process.poll() not in (None, 0):
                    return {"success": False, "message": "Discord falló al iniciar (proceso terminó inmediatamente)"}
                if not self._wait_for_process(self._candidate_process_names("discord")):
                    return {"success": False, "message": "Discord no inició correctamente (verificación fallida)"}

                # Verificar que Discord cargó su ventana principal (renderer)
                if self._has_electron_renderer("discord", timeout=8.0):
                    logger.info(f"Discord lanzado con ventana (PID: {process.pid})")
                    return {
                        "success": True,
                        "message": "Discord abierto correctamente (verificado)",
                        "pid": process.pid,
                        "method": "native"
                    }
                else:
                    # Proceso existe pero sin renderer: posible pantalla de actualización
                    logger.warning("Discord inició sin ventana principal (posible update pendiente)")
                    return {
                        "success": True,
                        "message": "Discord abierto (posible actualización pendiente — ejecuta: sudo pacman -S discord)",
                        "pid": process.pid,
                        "method": "native",
                        "needs_update": True
                    }
            except Exception as e:
                logger.error(f"Error lanzando Discord: {e}")
                return {"success": False, "message": f"Error: {str(e)}"}
        
        # Fallback: Flatpak
        try:
            process = subprocess.Popen(
                ["flatpak", "run", "com.discordapp.Discord"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env
            )
            if process.poll() not in (None, 0):
                return {"success": False, "message": "Discord (Flatpak) terminó inmediatamente"}
            if not self._wait_for_process(self._candidate_process_names("discord"), timeout=4.0):
                return {"success": False, "message": "Discord (Flatpak) no inició correctamente"}

            logger.info(f"Discord lanzado vía Flatpak (PID: {process.pid})")
            return {
                "success": True,
                "message": "Discord abierto (Flatpak, verificado)",
                "pid": process.pid,
                "method": "flatpak"
            }
        except FileNotFoundError:
            logger.error("Discord no encontrado (ni nativo ni Flatpak)")
            return {"success": False, "message": "Discord no está instalado"}
        except Exception as e:
            logger.error(f"Error con Flatpak: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def _focus_window(self, app_name: str) -> bool:
        """Intenta enfocar la ventana de una app ya abierta"""
        try:
            # Intentar con wmctrl primero
            result = subprocess.run(
                ["wmctrl", "-a", app_name],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                logger.info(f"Ventana '{app_name}' enfocada con wmctrl")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        try:
            # Fallback: xdotool
            result = subprocess.run(
                ["xdotool", "search", "--name", app_name, "windowactivate"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                logger.info(f"Ventana '{app_name}' enfocada con xdotool")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        return False

    def _launch_app_generic(self, app_name: str) -> Dict[str, any]:
        """Lanza una aplicación con comando específico o xdg-open"""
        # Resolver aliases (terminal→konsole, archivos→dolphin, etc.)
        resolved = resolve_alias(app_name)
        if resolved != app_name:
            logger.info(f"Alias '{app_name}' → '{resolved}'")
            app_name = resolved

        # Comandos específicos (from centralized config)
        app_commands = APP_COMMANDS
        
        # Navegadores: abrir nueva pestaña cuando ya están corriendo
        browser_commands = {
            "firefox": "firefox",
            "chrome": "google-chrome-stable",
            "chromium": "chromium",
        }
        
        try:
            env = os.environ.copy()
            for key, value in self.graphical_env.items():
                if value:
                    env[key] = value

            candidate_names = self._candidate_process_names(app_name)
            
            # Si la app ya está corriendo, intentar nueva pestaña o enfocar
            if self._is_any_process_running(candidate_names):
                logger.info(f"App '{app_name}' ya está corriendo")
                
                if app_name in browser_commands:
                    # Navegador ya abierto → abrir nueva pestaña (no nueva ventana)
                    browser_bin = browser_commands[app_name]
                    cmd = [browser_bin, "about:newtab"]
                    result = subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        env=env,
                        text=True,
                        timeout=5
                    )
                    if result.returncode != 0:
                        stderr_msg = (result.stderr or "").strip()[:180]
                        return {
                            "success": False,
                            "message": f"{app_name.capitalize()} no abrió nueva pestaña (rc={result.returncode}): {stderr_msg}"
                        }

                    # Verificación mínima: la app sigue corriendo tras comando
                    if not self._wait_for_process(candidate_names, timeout=1.2):
                        return {
                            "success": False,
                            "message": f"{app_name.capitalize()} no pasó la verificación post-lanzamiento"
                        }
                    
                    # Verificar que apareció una ventana (solo para apps con patrones definidos)
                    if self._should_check_window(app_name) and not self._check_window_exists(app_name, timeout=2.0):
                        return {
                            "success": False,
                            "message": f"{app_name.capitalize()} no mostró ventana después de nueva pestaña"
                        }

                    logger.info(f"Nueva pestaña de '{app_name}' abierta (verificado)")
                    return {
                        "success": True,
                        "message": f"{app_name.capitalize()} abierto (nueva pestaña)",
                        "method": "new_tab",
                        "already_running": True
                    }
                else:
                    # Intentar enfocar la ventana existente
                    focused = self._focus_window(app_name)
                    return {
                        "success": focused,
                        "message": f"{app_name.capitalize()} ya está abierto" if focused else f"{app_name.capitalize()} está abierto, pero no pude enfocarlo",
                        "already_running": True,
                        "method": "focus"
                    }
            
            # App no está corriendo, lanzar normalmente
            cmd = app_commands.get(app_name)
            if cmd:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env
                )
                if process.poll() not in (None, 0):
                    return {"success": False, "message": f"{app_name.capitalize()} falló al iniciar (proceso terminó inmediatamente)"}
                if not self._wait_for_process(candidate_names):
                    return {"success": False, "message": f"{app_name.capitalize()} no inició correctamente (verificación fallida)"}
                
                # Verificar que apareció una ventana (solo para apps con patrones definidos)
                if self._should_check_window(app_name) and not self._check_window_exists(app_name, timeout=3.0):
                    return {
                        "success": False,
                        "message": f"{app_name.capitalize()} no mostró ventana después de lanzarse"
                    }

                logger.info(f"App '{app_name}' lanzada: {' '.join(cmd)} (PID: {process.pid})")
                return {
                    "success": True,
                    "message": f"{app_name.capitalize()} abierto (verificado)",
                    "pid": process.pid,
                    "method": "specific_command"
                }
            
            # Web apps: open URL directly in native Firefox (reuses existing window)
            url = self.WEB_APP_URLS.get(app_name.lower())
            if url:
                # Use firefox directly to open in existing window as new tab
                firefox_bin = shutil.which("firefox") or "firefox"
                already_running = self._is_any_process_running(
                    self._candidate_process_names("firefox")
                )
                try:
                    subprocess.Popen(
                        [firefox_bin, url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        env=env,
                    )
                    site_name = url.replace("https://", "").replace("http://", "").split("/")[0]
                    if already_running:
                        msg = f"{app_name.capitalize()} abierto en nueva pestaña de Firefox"
                    else:
                        msg = f"Abriendo Firefox con {site_name}"
                    logger.info(f"Web app '{app_name}' → Firefox: {url}")
                    return {
                        "success": True,
                        "message": msg,
                        "method": "native_browser"
                    }
                except Exception as web_err:
                    logger.warning(f"Firefox falló para web app '{app_name}': {web_err}, continuando con lanzamiento genérico")

            # Fallback: xdg-open (generic)
            result = subprocess.run(
                ["xdg-open", app_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=env,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                stderr_msg = (result.stderr or "").strip()[:180]
                return {
                    "success": False,
                    "message": f"xdg-open falló para '{app_name}' (rc={result.returncode}): {stderr_msg}"
                }

            logger.info(f"App '{app_name}' lanzada vía xdg-open (comando verificado)")
            return {
                "success": True,
                "message": f"Intentando abrir {app_name} (comando verificado)",
                "method": "xdg-open"
            }
        except Exception as e:
            logger.error(f"Error lanzando {app_name}: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def _handle_command(self, command: Dict[str, any]) -> Dict[str, any]:
        """Procesa un comando recibido del cliente"""
        action = command.get("action")
        app = command.get("app", "").lower()
        
        logger.info(f"Comando recibido: action={action}, app={app}")
        
        if action == "open":
            if "discord" in app:
                return self._launch_discord()
            else:
                return self._launch_app_generic(app)
        
        elif action == "ping":
            return {"success": True, "message": "pong", "daemon_running": True}
        
        elif action == "status":
            return {
                "success": True,
                "daemon_running": True,
                "is_wayland": self.is_wayland,
                "dbus_available": bool(os.environ.get('DBUS_SESSION_BUS_ADDRESS')),
                "display": os.environ.get('DISPLAY'),
                "wayland_display": os.environ.get('WAYLAND_DISPLAY')
            }
        
        else:
            return {"success": False, "message": f"Acción desconocida: {action}"}
    
    def start(self):
        """Inicia el daemon y escucha en el socket"""
        socket_parent = Path(self.socket_path).parent
        socket_parent.mkdir(parents=True, exist_ok=True)

        # Limpiar socket anterior si existe
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        
        # Crear socket UNIX
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.socket_path)
        sock.listen(5)
        sock.settimeout(1.0)  # Timeout para poder chequear self.running
        
        # Permisos para que solo el usuario pueda conectarse
        os.chmod(self.socket_path, 0o600)
        
        logger.info(f"Escuchando en {self.socket_path}")
        
        try:
            while self.running:
                try:
                    conn, addr = sock.accept()
                    with conn:
                        # Recibir datos (máximo 4KB)
                        data = conn.recv(4096)
                        if not data:
                            continue
                        
                        # Parsear comando JSON
                        try:
                            command = json.loads(data.decode('utf-8'))
                            response = self._handle_command(command)
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON inválido: {e}")
                            response = {"success": False, "message": "JSON inválido"}
                        except Exception as e:
                            logger.error(f"Error procesando comando: {e}")
                            response = {"success": False, "message": str(e)}
                        
                        # Enviar respuesta
                        conn.sendall(json.dumps(response).encode('utf-8'))
                
                except socket.timeout:
                    # Timeout normal, continuar loop
                    continue
                except Exception as e:
                    logger.error(f"Error en conexión: {e}")
        
        finally:
            sock.close()
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
            logger.info("Daemon detenido")


def main():
    """Punto de entrada del daemon"""
    daemon = AppDaemon()
    try:
        daemon.start()
    except KeyboardInterrupt:
        logger.info("Daemon interrumpido por usuario")
    except Exception as e:
        logger.error(f"Error fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
