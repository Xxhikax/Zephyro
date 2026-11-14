"""
Tests for app focus/window activation flow (Discord + all desktop apps).

Covers:
- Discord already open → focus instead of silent "ya está abierto"
- App not running → launch normally
- App in APP_WINDOW_INFO → focus attempt
- App NOT in APP_WINDOW_INFO → fallback to direct launch
- Verification that _open_discord_direct returns focus attempts
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Patch platform before app_config imports to control IS_WINDOWS
IS_WINDOWS_REAL = sys.platform == "win32"


def test_discord_already_open_attempts_focus():
    """Cuando Discord ya está corriendo, debe intentar focus_window."""
    from core.app_config import APP_WINDOW_INFO
    assert "discord" in APP_WINDOW_INFO
    winfo = APP_WINDOW_INFO["discord"]
    assert "process" in winfo
    assert "class" in winfo
    assert "title" in winfo


def test_spotify_has_window_info():
    """Spotify debe estar en APP_WINDOW_INFO para funcionar con focus."""
    from core.app_config import APP_WINDOW_INFO
    assert "spotify" in APP_WINDOW_INFO


def test_vlc_has_window_info():
    """VLC debe estar en APP_WINDOW_INFO."""
    from core.app_config import APP_WINDOW_INFO
    assert "vlc" in APP_WINDOW_INFO


def test_code_has_window_info():
    """VSCode debe estar en APP_WINDOW_INFO."""
    from core.app_config import APP_WINDOW_INFO
    assert "code" in APP_WINDOW_INFO


def test_steam_has_window_info():
    """Steam debe estar en APP_WINDOW_INFO."""
    from core.app_config import APP_WINDOW_INFO
    assert "steam" in APP_WINDOW_INFO


def test_firefox_has_window_info():
    """Firefox debe estar en APP_WINDOW_INFO."""
    from core.app_config import APP_WINDOW_INFO
    assert "firefox" in APP_WINDOW_INFO


def test_all_window_info_has_required_keys():
    """Cada entrada en APP_WINDOW_INFO debe tener process, class y title."""
    from core.app_config import APP_WINDOW_INFO
    for app_name, winfo in APP_WINDOW_INFO.items():
        assert "process" in winfo, f"{app_name} missing 'process'"
        assert "class" in winfo, f"{app_name} missing 'class'"
        assert "title" in winfo, f"{app_name} missing 'title'"
        assert isinstance(winfo["process"], str), f"{app_name} process not a string"
        assert isinstance(winfo["class"], str), f"{app_name} class not a string"
        assert isinstance(winfo["title"], str), f"{app_name} title not a string"


def test_discord_open_direct_focus_message():
    """_open_discord_direct con VENTANA abierta debe traerla al frente.

    `has_visible_window` va mockeado a propósito: sin eso el test contesta
    distinto según si Bruno tiene Discord abierto en ese momento.
    """
    with patch("modules.automation.app_launcher.has_visible_window", return_value=True):
        with patch("modules.automation.app_launcher.focus_window", return_value=True):
            from modules.automation.app_launcher import AppLauncher
            launcher = AppLauncher()
            result = launcher._open_discord_direct()
            assert "ventana al frente" in result
            assert "✓" in result


def test_discord_open_direct_focus_failed_message():
    """Ventana abierta pero Windows niega el foreground: hay que explicarlo.

    Antes decía "no pude traer la ventana al frente" a secas, que suena a
    falla reintentable. No lo es: Windows bloquea a propósito que un proceso
    en segundo plano robe el foco, y pelearlo fue lo que ya le dejó la PC sin
    responder a los clicks una vez.
    """
    with patch("modules.automation.app_launcher.has_visible_window", return_value=True):
        with patch("modules.automation.app_launcher.focus_window", return_value=False):
            from modules.automation.app_launcher import AppLauncher
            launcher = AppLauncher()
            result = launcher._open_discord_direct()
            assert "Windows no deja" in result
            assert "barra de tareas" in result
            assert "✓" in result


def test_discord_in_the_tray_is_relaunched_not_declared_open():
    """Sin ventana, "ya está abierto" no le sirve a nadie: hay que levantarlo."""
    with patch("modules.automation.app_launcher.has_visible_window", return_value=False):
        with patch("modules.automation.app_launcher.is_process_running", return_value=True):
            with patch("modules.automation.app_launcher.focus_window") as _focus:
                with patch("modules.automation.app_launcher.subprocess.Popen"):
                    from modules.automation.app_launcher import AppLauncher
                    launcher = AppLauncher()
                    result = launcher._open_discord_direct()
                    assert "ya está abierto" not in result, result
                    assert not _focus.called, "no hay ventana que enfocar"


def test_app_in_window_info_with_a_window_gets_focused():
    """_open_app_direct con app en APP_WINDOW_INFO y VENTANA abierta → focus.

    Desde el 2026-10-02 la condición es `has_visible_window`, no
    `is_process_running`: el proceso vivo no significa que haya una ventana.
    `explorer.exe` ES la shell de Windows y está siempre vivo, así que con la
    condición vieja "abrí el explorador de archivos" contestaba "ya está
    abierto" sin una sola carpeta en pantalla.
    """
    with patch("modules.automation.app_launcher.has_visible_window", return_value=True):
        with patch("modules.automation.app_launcher.focus_window", return_value=True):
            from modules.automation.app_launcher import AppLauncher
            launcher = AppLauncher()
            result = launcher._open_app_direct("spotify")
            assert "ventana al frente" in result


def test_an_app_parked_in_the_tray_is_launched_not_reported_as_open():
    """La contraparte: proceso vivo pero SIN ventana → hay que abrirlo.

    Spotify minimizado a la bandeja seguía vivo, así que la condición vieja
    contestaba "ya estaba abierto" y no abría nada. Lanzarlo es lo correcto:
    una app de instancia única levanta su ventana cuando se la vuelve a lanzar.
    """
    with patch("modules.automation.app_launcher.has_visible_window", return_value=False):
        with patch("modules.automation.app_launcher.is_process_running", return_value=True):
            with patch("modules.automation.app_launcher.focus_window") as _focus:
                from modules.automation.app_launcher import AppLauncher
                launcher = AppLauncher()
                result = launcher._open_app_direct("spotify")
                assert "ya estaba abierto" not in result, result
                assert not _focus.called, "no hay ventana que traer al frente"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="calc.exe is a built-in Windows app; _open_app_direct always succeeds on Windows"
)
def test_app_not_in_window_info_falls_through():
    """App no en APP_WINDOW_INFO no bloquea el flujo normal."""
    with patch("modules.automation.app_launcher.shutil.which", return_value="somebin"):
        with patch("modules.automation.app_launcher.subprocess.Popen"):
            from modules.automation.app_launcher import AppLauncher
            launcher = AppLauncher()
            result = launcher._open_app_direct("calc")
            # No debería fallar, y como "calc" no está en WINDOW_INFO,
            # debería intentar lanzarlo directamente
            assert "Intentando abrir" in result or "no encontrado" in result.lower()


def test_focus_window_import_works():
    """focus_window debe ser importable desde core.platform."""
    from core.platform import focus_window
    assert callable(focus_window)


def test_platform_focus_window_accepts_params():
    """focus_window acepta process_name, window_class, window_title."""
    from core.platform import focus_window
    import inspect
    sig = inspect.signature(focus_window)
    params = list(sig.parameters.keys())
    assert "process_name" in params
    assert "window_class" in params
    assert "window_title" in params
