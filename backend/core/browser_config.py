"""
Configuracion centralizada del navegador para Playwright/Edge (Chromium).

Maneja deteccion automatica de entorno (Wayland/X11), perfil del usuario,
y variables de entorno necesarias para que Edge funcione desde systemd
o cualquier contexto sin sesion grafica directa.
"""
import json
import os
import sys
import shutil
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_IS_WIN = sys.platform == "win32"


def _detect_session_type() -> str:
    """Detecta si estamos en Wayland o X11."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    # Fallback: revisar XDG_SESSION_TYPE
    return os.environ.get("XDG_SESSION_TYPE", "unknown")


def _find_edge_executable() -> Optional[str]:
    """Busca el ejecutable de Microsoft Edge del sistema."""
    # Intentar shutil.which primero
    for name in ("msedge", "microsoft-edge-stable", "microsoft-edge"):
        edge_path = shutil.which(name)
        if edge_path:
            return edge_path

    if _IS_WIN:
        common_paths = [
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    else:
        common_paths = [
            "/usr/bin/microsoft-edge-stable",
            "/usr/bin/microsoft-edge",
            "/opt/microsoft/msedge/msedge",
        ]
    for path in common_paths:
        if path and Path(path).exists() and os.access(path, os.X_OK):
            return path
    return None


def _find_edge_user_profile() -> Optional[str]:
    """
    Encuentra el directorio de User Data de Edge del usuario.
    Edge/Chromium usan un directorio 'User Data' con perfiles dentro.
    """
    if _IS_WIN:
        base_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"
    else:
        base_dir = Path.home() / ".config" / "microsoft-edge"

    if base_dir.exists():
        # Verificar que existe el perfil Default
        default_profile = base_dir / "Default"
        if default_profile.exists():
            return str(base_dir)
        # Si no hay Default pero el directorio existe, aún es válido
        return str(base_dir)
    return None


# Puerto CDP por defecto para attach al Edge real del usuario.
ZEPHYRO_EDGE_CDP_PORT = int(os.environ.get("ZEPHYRO_EDGE_CDP_PORT", "9222"))

# Puerto CDP de la instancia dedicada de automatización (separado del 9222
# del Edge real para que ambas instancias convivan sin competir).
ZEPHYRO_AUTOMATION_CDP_PORT = int(os.environ.get("ZEPHYRO_AUTOMATION_CDP_PORT", "9223"))


def _default_automation_profile_path() -> str:
    """
    Directorio User Data dedicado para la instancia de Edge de automatización.

    Es un perfil persistente separado del User Data real: los logins que el
    usuario haga una vez en esa instancia quedan guardados entre sesiones.
    """
    env_path = os.environ.get("ZEPHYRO_AUTOMATION_PROFILE_PATH")
    if env_path:
        return env_path
    if _IS_WIN:
        base = os.environ.get("LOCALAPPDATA", "")
    else:
        base = str(Path.home() / ".local" / "share")
    return str(Path(base) / "Zephyro" / "EdgeAutomation")


def _get_active_edge_profile(user_data_dir: Optional[str]) -> str:
    """
    Lee el último perfil activo de Edge desde el archivo Local State.

    Retorna el nombre del perfil (p.ej. "Default", "Profile 1").
    Fallback a "Default" si el archivo no existe o no se puede parsear.
    """
    if not user_data_dir:
        return "Default"
    try:
        local_state = Path(user_data_dir) / "Local State"
        if local_state.exists():
            with open(local_state, encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            return data.get("profile", {}).get("last_used") or "Default"
    except Exception as exc:
        logger.debug(f"No se pudo leer Local State para detectar perfil activo: {exc}")
    return "Default"


def ensure_display_env():
    """
    Asegura que las variables de entorno de display esten configuradas.
    Critico cuando se ejecuta desde systemd u otro contexto sin sesion grafica.
    """
    if _IS_WIN:
        # Windows maneja display automáticamente, no necesita vars de entorno
        return

    session_type = _detect_session_type()

    if session_type == "wayland":
        os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
        os.environ.setdefault("DISPLAY", ":0")
    elif session_type == "x11":
        os.environ.setdefault("DISPLAY", ":0")
    else:
        # Sin sesion grafica detectada: configurar defaults para intentar
        os.environ.setdefault("DISPLAY", ":0")
        os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
        logger.warning(
            "No se detecto sesion grafica. "
            f"DISPLAY={os.environ.get('DISPLAY')}, "
            f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')}"
        )

    # XDG_RUNTIME_DIR es necesario para Wayland sockets
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not xdg_runtime:
        uid = os.getuid()
        fallback = f"/run/user/{uid}"
        if Path(fallback).exists():
            os.environ["XDG_RUNTIME_DIR"] = fallback
            logger.info(f"XDG_RUNTIME_DIR configurado a {fallback}")

    # DBUS es necesario para comunicacion con navegadores en Linux
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        xdg_rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        bus_path = f"unix:path={xdg_rt}/bus"
        if Path(f"{xdg_rt}/bus").exists():
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = bus_path
            logger.info(f"DBUS_SESSION_BUS_ADDRESS configurado a {bus_path}")

    logger.debug(
        f"Display env: session={session_type}, "
        f"DISPLAY={os.environ.get('DISPLAY')}, "
        f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')}, "
        f"XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR')}"
    )


@dataclass
class BrowserConfig:
    """Configuracion centralizada del navegador (Edge/Chromium)."""

    # Modo de ejecucion
    headless: bool = False

    # Directorio User Data de Edge del usuario (None = auto-detect)
    user_profile_path: Optional[str] = None

    # Ejecutable de Edge (None = auto-detect)
    edge_executable: Optional[str] = None

    # Timeouts en milisegundos
    timeout_navigation: int = 60000
    timeout_element: int = 30000
    timeout_launch: int = 60000

    # Viewport
    viewport_width: int = 1280
    viewport_height: int = 720

    # Directorios
    screenshot_dir: str = "backend/storage/playwright_screenshots"
    debug_dir: str = "backend/storage/playwright_debug"

    # Limpieza
    screenshot_retention_days: int = 7
    max_screenshot_files: int = 50

    # Logging
    log_file: str = "playwright.log"
    debug_mode: bool = False

    # Edge/Chromium launch args
    edge_args: list = field(default_factory=list)

    # Puerto CDP para attach al Edge real del usuario
    cdp_port: int = ZEPHYRO_EDGE_CDP_PORT

    # Directorio User Data dedicado para automatización (None = default por plataforma)
    automation_profile_path: Optional[str] = None

    # Puerto CDP de la instancia dedicada de automatización
    automation_cdp_port: int = ZEPHYRO_AUTOMATION_CDP_PORT

    def __post_init__(self):
        """Auto-detectar configuracion si no se especifico."""
        # Asegurar variables de entorno de display
        ensure_display_env()

        # Auto-detect Edge executable
        if self.edge_executable is None:
            self.edge_executable = _find_edge_executable()

        # Auto-detect user profile real (User Data de Edge). NO clonamos:
        # el control se hace vía CDP attach al Edge real del usuario, lo que
        # preserva cookies, logins, extensiones y sesiones sin duplicar nada.
        if self.user_profile_path is None:
            self.user_profile_path = _find_edge_user_profile()

        # Perfil dedicado de automatización (separado del User Data real)
        if self.automation_profile_path is None:
            self.automation_profile_path = _default_automation_profile_path()

        # Debug mode from env
        if os.environ.get("ZEPHYRO_DEBUG", "").lower() in ("1", "true", "yes", "on"):
            self.debug_mode = True

        # Default Edge/Chromium args
        if not self.edge_args:
            self.edge_args = [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-default-apps",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-features=TranslateUI",
            ]
            # Wayland support para Edge en Linux
            if not _IS_WIN and _detect_session_type() == "wayland":
                self.edge_args.append("--ozone-platform=wayland")

        logger.info(
            f"BrowserConfig: headless={self.headless}, "
            f"edge={self.edge_executable}, "
            f"profile={self.user_profile_path}"
        )

    @classmethod
    def for_visible(cls) -> "BrowserConfig":
        """Config para uso interactivo con ventana visible."""
        return cls(headless=False)

    @classmethod
    def for_headless(cls) -> "BrowserConfig":
        """Config para automatizacion en background."""
        return cls(
            headless=True,
            timeout_navigation=30000,
            timeout_element=15000,
        )

    @classmethod
    def for_debugging(cls) -> "BrowserConfig":
        """Config para debugging con timeouts generosos."""
        return cls(
            headless=False,
            timeout_navigation=120000,
            timeout_element=60000,
            debug_mode=True,
        )

    @property
    def viewport(self) -> dict:
        return {"width": self.viewport_width, "height": self.viewport_height}
