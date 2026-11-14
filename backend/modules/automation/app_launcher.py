#!/usr/bin/env python3
"""
AppLauncher: Sistema simplificado de lanzamiento de aplicaciones
Se comunica con zephyro_app_daemon para lanzar apps con contexto gráfico completo
"""
import os
import json
import shutil
import socket
import subprocess
import logging
from typing import Optional, Dict, List

from core.app_config import (
    WEB_APP_URLS as _CFG_WEB_APP_URLS,
    APP_WINDOW_INFO,
    resolve_alias,
)
from core.platform import (
    IS_WINDOWS, popen_detach_kwargs, popen_launch_kwargs, is_process_running,
    open_url_default, focus_window, has_visible_window,
)

logger = logging.getLogger(__name__)

# Configuración del daemon (Linux-only: Unix sockets)
DAEMON_TIMEOUT = 2.0  # segundos

if IS_WINDOWS:
    DAEMON_SOCKET = None
else:
    def _default_daemon_socket() -> str:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            return os.path.join(runtime_dir, "zephyro-apps.sock")
        return "/tmp/zephyro-apps.sock"

    DAEMON_SOCKET = os.environ.get("ZEPHYRO_DAEMON_SOCKET", _default_daemon_socket())


class AppLauncher:
    """
    Lanzador de aplicaciones que se comunica con zephyro_app_daemon.
    Fallback a ejecución directa si el daemon no está disponible.
    """
    # Mapping of web apps to URLs (from centralized config).
    WEB_APP_URLS = _CFG_WEB_APP_URLS

    def __init__(self):
        # PID REAL del último lanzamiento, el que devolvió Popen. Antes el
        # verificador lo re-adivinaba por nombre 300 ms después: una carrera y
        # un match ambiguo (cualquier `powershell.exe` del sistema servía).
        self.last_launch_pid: Optional[int] = None
        self.is_wayland = not IS_WINDOWS and os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
        self.daemon_available = False if IS_WINDOWS else self._check_daemon()
        
        if self.daemon_available:
            logger.info("Conectado a zephyro_app_daemon")
        else:
            logger.warning("Daemon no disponible, usando fallback directo")
    
    def _check_daemon(self) -> bool:
        """Verifica si el daemon está corriendo"""
        try:
            response = self._send_daemon_command({"action": "ping"})
            return response.get("daemon_running", False)
        except:
            return False

    def _get_socket_candidates(self) -> List[str]:
        """Return list of possible socket paths in order of preference."""
        if IS_WINDOWS:
            return []  # No Unix sockets en Windows
        candidates = []
        # 1. Explicit environment variable
        env_socket = os.environ.get("ZEPHYRO_DAEMON_SOCKET")
        if env_socket:
            candidates.append(env_socket)
        # 2. XDG_RUNTIME_DIR (user session)
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            candidates.append(os.path.join(runtime_dir, "zephyro-apps.sock"))
        # 3. Systemd user runtime directory
        candidates.append(f"/run/user/{os.getuid()}/zephyro-apps.sock")
        # 4. Traditional /tmp
        candidates.append("/tmp/zephyro-apps.sock")
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for cand in candidates:
            if cand not in seen:
                seen.add(cand)
                unique.append(cand)
        return unique
    
    def _send_daemon_command(self, command: Dict) -> Dict:
        """Envía un comando al daemon y retorna la respuesta"""
        candidates = self._get_socket_candidates()
        last_error = None
        for socket_path in candidates:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(DAEMON_TIMEOUT)
                sock.connect(socket_path)
                
                sock.sendall(json.dumps(command).encode('utf-8'))
                data = sock.recv(4096)
                response = json.loads(data.decode('utf-8'))
                sock.close()
                return response
            except FileNotFoundError:
                last_error = ConnectionError(f"Daemon socket no encontrado en {socket_path}")
                continue
            except socket.timeout:
                last_error = ConnectionError(f"Daemon no responde (timeout) en {socket_path}")
                continue
            except ConnectionRefusedError:
                last_error = ConnectionError(f"Conexión rechazada en {socket_path}")
                continue
            except Exception as e:
                last_error = ConnectionError(f"Error comunicando con daemon en {socket_path}: {e}")
                continue
        
        # If all candidates failed
        if last_error:
            raise last_error
        raise ConnectionError("No hay sockets candidatos para conectar al daemon")
    
    def _is_app_running(self, process_name: str) -> bool:
        """Verifica si una aplicación está ejecutándose (cross-platform)."""
        return is_process_running(process_name)

    def _spawn_and_confirm(self, argv: list, app_name: str, *, via: str = "") -> str:
        """Lanza `argv` y reporta LO QUE PASÓ, no lo que se intentó.

        El mensaje viejo era `✓ Intentando abrir {app} (fallback directo)`:
        un tilde de éxito sobre una afirmación que nadie había comprobado. Para
        `powershell` el proceso se moría en el acto y el usuario leía un ✓.

        La espera corta es el precio de no mentir: si el proceso ya terminó
        cuando volvemos a mirar, no arrancó. Que además tenga VENTANA lo
        confirma el VerificationAgent asincrónico, con el PID real que
        guardamos acá.
        """
        import time as _time

        proc = subprocess.Popen(argv, **popen_launch_kwargs(argv[0]))
        self.last_launch_pid = proc.pid
        suffix = f" ({via})" if via else ""

        _time.sleep(0.8)
        if proc.poll() is not None:
            self.last_launch_pid = None
            logger.warning(
                f"{app_name}: el proceso terminó con código {proc.returncode} "
                f"apenas lanzado (argv={argv[0]})"
            )
            return (
                f"✗ Lancé {app_name} pero el proceso terminó enseguida, "
                f"así que no te quedó ninguna ventana."
            )
        return f"✓ He abierto {app_name}{suffix}."

    def get_process_pid(self, name_substring: str) -> Optional[int]:
        """
        Devuelve el PID del proceso más reciente cuyo nombre contenga
        `name_substring`. Útil para verificación post-launch. None si
        no hay proceso matching.
        """
        import psutil
        if not name_substring:
            return None
        name_lower = name_substring.lower()
        best_pid: Optional[int] = None
        best_ct: float = 0.0
        for proc in psutil.process_iter(['pid', 'name', 'create_time']):
            try:
                pname = (proc.info.get('name') or '').lower()
                if name_lower in pname:
                    ct = proc.info.get('create_time') or 0.0
                    if ct > best_ct:
                        best_ct = ct
                        best_pid = proc.info.get('pid')
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return best_pid
    
    def _get_env_for_electron(self) -> dict:
        """Retorna variables de entorno para apps Electron en Wayland"""
        env = os.environ.copy()
        if self.is_wayland:
            env['ELECTRON_OZONE_PLATFORM_HINT'] = 'x11'
        return env
    
    def open_app(self, app_name: str) -> str:
        """
        Abre una aplicación. Intenta usar el daemon primero, fallback a directo.
        Para web apps conocidas, siempre usa open_url_in_browser directamente
        para garantizar un mensaje de respuesta consistente (bypasa el daemon).
        """
        app_name = app_name.lower().strip()
        # Un PID viejo es peor que ninguno: haría que el verificador confirme
        # esta apertura con la evidencia de la anterior.
        self.last_launch_pid = None

        # Resolve aliases (terminal→konsole, archivos→dolphin, etc.)
        resolved = resolve_alias(app_name)
        if resolved != app_name:
            logger.info(f"Alias '{app_name}' → '{resolved}'")
            app_name = resolved

        # Web apps: bypass daemon to guarantee a consistent response message.
        url = self.WEB_APP_URLS.get(app_name)
        if url:
            return self.open_url_in_browser(url)

        # Intentar vía daemon
        if self.daemon_available:
            try:
                response = self._send_daemon_command({
                    "action": "open",
                    "app": app_name
                })
                
                if response.get("success"):
                    msg = response.get("message", "Aplicación abierta")
                    logger.info(f"Daemon abrió {app_name}: {msg}")
                    return f"✓ {msg}"
                else:
                    error = response.get("message", "Error desconocido")
                    logger.error(f"Daemon falló abriendo {app_name}: {error}")
                    return f"✗ {error}"
            
            except ConnectionError as e:
                logger.warning(f"Error con daemon: {e}, usando fallback")
                self.daemon_available = False  # Marcar como no disponible
        
        # Fallback: Ejecución directa
        return self._open_app_direct(app_name)

    def is_browser_running(self, browser: str = "msedge") -> bool:
        """Check if a browser process is currently running."""
        return self._is_app_running(browser)

    def open_url_in_browser(self, url: str, browser: str = "msedge") -> str:
        """
        Opens a URL in the native browser.
        If the browser is already running, opens as a new tab.
        If not running, starts the browser with the URL.
        """
        try:
            browser_path = shutil.which(browser)
            if not browser_path:
                # Edge en Windows: buscar directamente en Program Files
                if browser == "msedge" and IS_WINDOWS:
                    for candidate in (
                        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                        os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                    ):
                        if candidate and os.path.exists(candidate):
                            browser_path = candidate
                            break
            if not browser_path:
                # Fallback: abrir en navegador predeterminado del sistema
                open_url_default(url)
                return f"✓ Abriendo {url} en el navegador predeterminado"

            already_running = self.is_browser_running(browser)
            subprocess.Popen(
                [browser_path, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **popen_detach_kwargs()
            )

            site_name = url.replace("https://", "").replace("http://", "").rstrip("/")
            if already_running:
                return f"✓ Abriendo {site_name} en nueva pestaña de {browser}"
            else:
                return f"✓ Abriendo {site_name} en {browser}"
        except Exception as e:
            logger.error(f"Error abriendo URL {url} en {browser}: {e}")
            return f"✗ Error abriendo {url}: {e}"

    def _open_app_direct(self, app_name: str) -> str:
        """Abre aplicación directamente (fallback sin daemon)"""
        self.last_launch_pid = None
        # Resolve aliases again in case called directly
        resolved = resolve_alias(app_name)
        if resolved != app_name:
            logger.info(f"Alias '{app_name}' → '{resolved}' (direct fallback)")
            app_name = resolved

        # Discord específico
        if "discord" in app_name:
            return self._open_discord_direct()

        # Apps con info de ventana: si ya tienen VENTANA, intentar focus.
        #
        # La pregunta es "¿hay algo en pantalla?", no "¿el proceso vive?".
        # `explorer.exe` es la shell de Windows: está siempre vivo, así que con
        # el chequeo por proceso "abrí el explorador de archivos" contestaba
        # "ya está abierto" aunque no hubiera ni una carpeta. Misma forma para
        # cualquier app minimizada a la bandeja.
        winfo = APP_WINDOW_INFO.get(app_name)
        if winfo:
            process_name = winfo.get("process", app_name)
            if has_visible_window(process_name, winfo.get("class")):
                if focus_window(
                    process_name,
                    window_class=winfo.get("class"),
                    window_title=winfo.get("title"),
                ):
                    return f"✓ {app_name} ya estaba abierto (ventana al frente)."
                return f"✓ {app_name} ya está abierto (pero no pude traer la ventana al frente)."
        
        # Browsers: handle specifically (native Edge/browser with homepage or new tab)
        browser_names = {"firefox", "chrome", "chromium", "edge", "msedge", "navegador", "browser"}
        if app_name in browser_names:
            return self._open_browser_direct(app_name)
        
        # Web apps: open in native browser (reuses existing Edge window)
        url = self.WEB_APP_URLS.get(app_name.lower())
        if url:
            return self.open_url_in_browser(url)

        # Guard: NO abrir como app strings que claramente no son apps. Frases con
        # espacios ("reproducir música", "stop music") o palabras genéricas de
        # media ("música", "canción") llegan acá por STT mal interpretado o por
        # el LLM; abrirlas con Start-Process es peligroso y reporta falso éxito.
        _norm = app_name.strip().lower()
        _NOT_APPS = {
            "musica", "música", "music", "cancion", "canción", "audio", "sonido",
            "reproducir", "pausa", "pause", "reproduccion", "reproducción",
            "tema", "playlist", "lista",
        }
        if " " in _norm or _norm in _NOT_APPS:
            logger.info(f"open_app: '{app_name}' no es una app reconocida; no se abre por shell")
            return f"✗ No reconozco '{app_name}' como una aplicación."

        # Windows: buscar binarios Office y apps comunes en locations conocidos
        if IS_WINDOWS:
            office_exe = _find_windows_desktop_app(app_name)
            if office_exe:
                try:
                    return self._spawn_and_confirm([office_exe], app_name)
                except Exception as e:
                    logger.error(f"Error abriendo {app_name} desde {office_exe}: {e}")

        # Lanzar el binario directamente (xdg-open es para archivos/URLs, no binarios)
        try:
            if shutil.which(app_name):
                return self._spawn_and_confirm([app_name], app_name)
            # Windows: último fallback con PowerShell Start-Process (resuelve
            # vía registry shell handler, ej. "winword" sin .exe completo).
            if IS_WINDOWS:
                try:
                    # `-PassThru` devuelve el PID: sin él, returncode 0 solo
                    # dice que PowerShell despachó el pedido, no que la app
                    # haya quedado viva — el mismo "✓ sin mirar" de antes.
                    result = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-Command",
                         f"(Start-Process -FilePath '{app_name}' -PassThru).Id"],
                        capture_output=True, text=True, timeout=5,
                        **popen_detach_kwargs()
                    )
                    if result.returncode == 0:
                        pid = (result.stdout or "").strip()
                        self.last_launch_pid = int(pid) if pid.isdigit() else None
                        return f"✓ He abierto {app_name} (via PowerShell)."
                    logger.warning(f"PowerShell Start-Process {app_name} falló: {result.stderr.strip()[:120]}")
                except Exception as e:
                    logger.warning(f"PowerShell fallback falló para {app_name}: {e}")
            return f"✗ No pude abrir {app_name}: no encontrado en PATH ni en ubicaciones conocidas."
        except Exception as e:
            logger.error(f"Error abriendo {app_name}: {e}")
            return f"✗ No pude abrir {app_name}."
    
    def _has_electron_renderer(self, app_name: str, timeout: float = 8.0) -> bool:
        """Verifica que una app Electron tenga renderer (ventana real, no splash)."""
        import time
        import psutil
        deadline = time.monotonic() + timeout
        app_lower = app_name.lower()
        while time.monotonic() < deadline:
            try:
                for proc in psutil.process_iter(['cmdline']):
                    try:
                        cmdline = " ".join(proc.info['cmdline'] or [])
                        if "type=renderer" in cmdline and app_lower in cmdline.lower():
                            return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _open_discord_direct(self) -> str:
        """Abre Discord directamente (fallback sin daemon)."""
        winfo = APP_WINDOW_INFO.get("discord", {})
        _proc = winfo.get("process", "Discord")
        # Con ventana abierta → traerla al frente. Sin ventana (en la bandeja)
        # → lanzarlo, que es lo que la levanta. Antes la condición era "el
        # proceso vive", así que Discord parkeado en la bandeja contestaba "ya
        # está abierto" y no aparecía nada.
        if has_visible_window(_proc, winfo.get("class")):
            if focus_window(_proc, window_class=winfo.get("class"),
                            window_title=winfo.get("title")):
                return "✓ Discord ya estaba abierto (ventana al frente)."
            # Ya no es "no sé qué pasó": la ventana existe y Windows negó el
            # foreground. Es una restricción deliberada del sistema contra un
            # proceso en background, no una falla que se pueda reintentar.
            return (
                "✓ Discord ya está abierto. No puedo traerlo al frente yo "
                "mismo —Windows no deja que un proceso en segundo plano robe "
                "el foco— así que te lo dejo parpadeando en la barra de tareas."
            )
        if self._is_app_running(_proc):
            logger.info("Discord corriendo sin ventana (bandeja): se relanza para levantarla")

        env = self._get_env_for_electron()

        if IS_WINDOWS:
            import glob as _glob
            local_app = os.environ.get("LOCALAPPDATA", "")
            discord_path = os.path.join(local_app, "Discord", "Update.exe")
            if os.path.exists(discord_path):
                cmd = [discord_path, "--processStart", "Discord.exe"]
            else:
                # Update.exe not found: try versioned app-x.x.x/Discord.exe paths
                _versioned = _glob.glob(os.path.join(local_app, "Discord", "app-*", "Discord.exe"))
                if _versioned:
                    _versioned.sort(reverse=True)  # newest version first
                    cmd = [_versioned[0]]
                else:
                    cmd = None
        else:
            discord_path = "/opt/discord/Discord"
            if not os.path.exists(discord_path):
                # Try common alternate paths (Arch/Flatpak)
                for _alt in ["/usr/bin/discord", "/usr/bin/Discord", shutil.which("discord") or ""]:
                    if _alt and os.path.exists(_alt):
                        discord_path = _alt
                        break
            cmd = [discord_path, "--no-sandbox", "--disable-gpu"] if os.path.exists(discord_path) else None

        if cmd:
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **popen_detach_kwargs(),
                    env=env
                )
                # Verificar que Discord cargó ventana principal
                if self._has_electron_renderer("discord", timeout=8.0):
                    logger.info("Discord abierto (fallback directo)")
                    return "✓ He abierto Discord."
                elif self._is_app_running("Discord"):
                    logger.warning("Discord sin renderer (posible update)")
                    return "✓ Discord abierto (posible actualización pendiente)."
                else:
                    return "✗ Discord no inició correctamente."
            except Exception as e:
                logger.error(f"Error abriendo Discord: {e}")

        return "✗ No se pudo abrir Discord."

    def _open_browser_direct(self, browser: str = "msedge") -> str:
        """Opens a browser: if not running → launch (user homepage), if running → new tab."""
        # Normalize aliases
        if browser in ("navegador", "browser"):
            browser = "msedge"
        
        browser_path = shutil.which(browser)
        if not browser_path and browser == "msedge" and IS_WINDOWS:
            for candidate in (
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            ):
                if candidate and os.path.exists(candidate):
                    browser_path = candidate
                    break
        if not browser_path:
            return f"✗ {browser} no encontrado en el sistema"
        
        if self.is_browser_running(browser):
            # Already running → open a new tab page in the existing window
            try:
                new_tab_url = "edge://newtab" if "edge" in browser.lower() else "about:newtab"
                subprocess.Popen(
                    [browser_path, new_tab_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **popen_detach_kwargs()
                )
                return f"✓ Nueva pestaña abierta en {browser}"
            except Exception as e:
                logger.error(f"Error abriendo nueva pestaña en {browser}: {e}")
                return f"✗ Error abriendo nueva pestaña: {e}"
        else:
            # Not running → launch browser (opens with user's configured homepage)
            try:
                subprocess.Popen(
                    [browser_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **popen_detach_kwargs()
                )
                return f"✓ Abriendo {browser}"
            except Exception as e:
                logger.error(f"Error abriendo {browser}: {e}")
                return f"✗ Error abriendo {browser}: {e}"
    
    def open_discord(self) -> str:
        """
        Abre Discord (método legacy, ahora redirige a open_app).
        Mantenido por compatibilidad.
        """
        return self.open_app("discord")


# ── Windows desktop app discovery ─────────────────────────────────────────────

_WINDOWS_DESKTOP_APPS: Dict[str, List[str]] = {
    # Microsoft Office (Office16 = 365/2019/2021/2024)
    "word": [
        r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\WINWORD.EXE",
        r"C:\Program Files\Microsoft Office\Office16\WINWORD.EXE",
    ],
    "excel": [
        r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\EXCEL.EXE",
        r"C:\Program Files\Microsoft Office\Office16\EXCEL.EXE",
    ],
    "powerpoint": [
        r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"C:\Program Files\Microsoft Office\Office16\POWERPNT.EXE",
    ],
    "outlook": [
        r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\OUTLOOK.EXE",
    ],
    "onenote": [
        r"C:\Program Files\Microsoft Office\root\Office16\ONENOTE.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\ONENOTE.EXE",
    ],
    # System apps
    "notepad":  [r"C:\Windows\System32\notepad.exe", r"C:\Windows\notepad.exe"],
    "paint":    [r"C:\Windows\System32\mspaint.exe"],
    "wordpad":  [r"C:\Program Files\Windows NT\Accessories\wordpad.exe"],
    "explorer": [r"C:\Windows\explorer.exe"],
    "calc":     [r"C:\Windows\System32\calc.exe"],
}


def _find_windows_desktop_app(app_name: str) -> Optional[str]:
    """
    Busca el ejecutable de una app de escritorio Windows.
    Devuelve el path completo o None si no se encuentra.
    """
    candidates = _WINDOWS_DESKTOP_APPS.get(app_name.lower())
    if not candidates:
        return None
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


# Configurar logging básico
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(name)s: %(message)s'
)
