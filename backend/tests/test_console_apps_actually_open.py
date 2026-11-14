"""Abrir una app de consola la mataba, y el launcher reportaba "✓".

Incidente del 2026-10-02: Bruno pide "abre powershell" y lee
`✓ Intentando abrir powershell (fallback directo).` No se abrió nada.

Tres defectos encadenados, los tres de la misma familia que la Fase B — el
que decide y el que ejecuta miran universos distintos:

1. **DETACHED_PROCESS mata las apps de consola.** `popen_detach_kwargs` fue
   escrito para apps GUI. Una app de consola lanzada así no hereda la consola
   del padre ni recibe una propia: arranca sin stdin y termina al instante.
   Medido:

       DETACHED_PROCESS   + DEVNULL  -> pid=7584  vivo=False  ventanas=0
       CREATE_NEW_CONSOLE (sin pipe) -> pid=1644  vivo=True   ventanas=1

2. **El "✓" no miraba nada.** `✓ Intentando abrir X` afirma un intento, con
   el tilde de éxito adelante. Nadie comprobaba que el proceso siguiera vivo.

3. **El PID se tiraba y se re-adivinaba por nombre.** `Popen` devuelve el PID
   exacto; `_publish_app_launched` lo descartaba y 300 ms después buscaba por
   substring de nombre. Carrera, y ambiguo: cualquier `powershell.exe` del
   sistema —incluidos los que lanza el propio Zephyro— matcheaba.

Y el cuarto, en `explorer`: preguntar "¿el proceso vive?" cuando la pregunta
del usuario es "¿tengo una ventana?". `explorer.exe` ES la shell de Windows,
está siempre vivo, así que "abrí el explorador de archivos" contestaba "ya
está abierto" sin una sola carpeta en pantalla.
"""
import subprocess
import sys
from unittest.mock import patch

import pytest

from core import platform as plat
from core.platform import (
    is_console_app,
    popen_detach_kwargs,
    popen_launch_kwargs,
)

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="los flags de creación son de Windows"
)

CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008


# ── Quién necesita consola ──────────────────────────────────────────────
@WINDOWS_ONLY
@pytest.mark.parametrize("app", ["powershell", "cmd"])
def test_console_apps_are_recognized(app):
    assert is_console_app(app) is True, f"{app} es de consola y hay que darle una"


@WINDOWS_ONLY
@pytest.mark.parametrize("app", ["explorer", "notepad", "calc", "taskmgr"])
def test_gui_apps_are_not_given_a_console(app):
    """La contraparte legítima: una consola de más es una ventana negra fea."""
    assert is_console_app(app) is False


@WINDOWS_ONLY
def test_an_unknown_name_does_not_claim_a_console():
    assert is_console_app("no-existe-este-binario") is False
    assert is_console_app("") is False


# ── Los flags ───────────────────────────────────────────────────────────
@WINDOWS_ONLY
def test_a_console_app_gets_a_new_console_and_no_pipes():
    kw = popen_launch_kwargs("powershell")
    assert kw["creationflags"] & CREATE_NEW_CONSOLE, "sin consola propia se muere"
    assert not kw["creationflags"] & DETACHED_PROCESS, (
        "DETACHED_PROCESS es exactamente lo que la mataba"
    )
    assert "stdout" not in kw, "una consola sin stdout no le sirve a nadie"


@WINDOWS_ONLY
def test_a_gui_app_stays_detached_and_silent():
    kw = popen_launch_kwargs("notepad")
    assert kw["creationflags"] & DETACHED_PROCESS
    assert not kw["creationflags"] & CREATE_NEW_CONSOLE
    assert kw["stdout"] is subprocess.DEVNULL


@WINDOWS_ONLY
def test_detach_kwargs_are_left_untouched_for_their_own_callers():
    """`popen_detach_kwargs` sigue siendo lo que era: esto no es un rename."""
    assert popen_detach_kwargs()["creationflags"] & DETACHED_PROCESS


# ── "Ya está abierto" es sobre ventanas, no sobre procesos ──────────────
def test_the_shell_process_is_not_an_open_window(monkeypatch):
    """explorer.exe vivo + cero carpetas = hay que abrir una, no felicitarse."""
    monkeypatch.setattr(plat, "_pid_has_visible_window", lambda pid: False)

    class _P:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}

    fake = type("m", (), {
        "process_iter": staticmethod(lambda f=None: [_P(1, "explorer.exe")]),
        "NoSuchProcess": type("NoSuchProcess", (Exception,), {}),
        "AccessDenied": type("AccessDenied", (Exception,), {}),
    })
    monkeypatch.setitem(sys.modules, "psutil", fake)

    assert plat.is_process_running("explorer") is True
    assert plat.has_visible_window("explorer") is False, (
        "el proceso de la shell no es una ventana del usuario"
    )


def test_a_real_window_is_reported(monkeypatch):
    """Contraparte legítima: con una carpeta abierta, no se relanza."""
    monkeypatch.setattr(plat, "_pid_has_visible_window", lambda pid: True)

    class _P:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}

    fake = type("m", (), {
        "process_iter": staticmethod(lambda f=None: [_P(1, "explorer.exe")]),
        "NoSuchProcess": type("NoSuchProcess", (Exception,), {}),
        "AccessDenied": type("AccessDenied", (Exception,), {}),
    })
    monkeypatch.setitem(sys.modules, "psutil", fake)

    assert plat.has_visible_window("explorer") is True


def test_an_empty_name_matches_nothing():
    assert plat.has_visible_window("") is False


# ── El mensaje dice lo que pasó ─────────────────────────────────────────
class _FakeProc:
    def __init__(self, pid, rc):
        self.pid, self.returncode, self._rc = pid, rc, rc

    def poll(self):
        return self._rc


@pytest.fixture
def launcher():
    from modules.automation.app_launcher import AppLauncher
    return AppLauncher()


def test_a_process_that_died_is_not_reported_with_a_checkmark(launcher):
    """El bug exacto: ✓ sobre un proceso que ya no existía."""
    with patch("modules.automation.app_launcher.subprocess.Popen",
               return_value=_FakeProc(4242, 1)):
        msg = launcher._spawn_and_confirm(["powershell"], "powershell")

    assert msg.startswith("✗"), f"sigue mintiendo: {msg!r}"
    assert "terminó enseguida" in msg
    assert launcher.last_launch_pid is None, "un PID muerto no es evidencia"


def test_a_process_that_lives_is_reported_as_open(launcher):
    with patch("modules.automation.app_launcher.subprocess.Popen",
               return_value=_FakeProc(4242, None)):
        msg = launcher._spawn_and_confirm(["notepad"], "notepad")

    assert msg.startswith("✓")
    assert "Intentando" not in msg, "'intentando' no es un resultado"
    assert launcher.last_launch_pid == 4242, (
        "el PID real de Popen es la evidencia; adivinarlo por nombre es una carrera"
    )


def test_the_pid_is_cleared_before_each_launch(launcher):
    """Un PID viejo confirmaría esta apertura con la evidencia de la anterior."""
    launcher.last_launch_pid = 999
    with patch.object(launcher, "_open_app_direct", return_value="✓") as direct:
        launcher.open_app("notepad")
    assert direct.called
    assert launcher.last_launch_pid is None


# ── El verificador no puede pedirle ventana a una consola ───────────────
@WINDOWS_ONLY
@pytest.mark.asyncio
async def test_a_live_console_app_is_not_called_a_crash():
    """En Windows 11 la consola la hostea WindowsTerminal.exe, que no es hijo.

    Medido: powershell queda con una `PseudoConsoleWindow` de 0x0 y título
    vacío. Exigir ventana por PID daría un falso "crash" en CADA consola, y
    una alarma que suena siempre enseña a ignorar la alarma.
    """
    import os

    from agents.verification_agent import VerificationAgent

    agent = VerificationAgent.__new__(VerificationAgent)
    res = await agent._verify_app_launched({
        "extra": {"app": "powershell", "process_name": "powershell",
                  "pid": os.getpid()},
    })

    assert res["ok"] is True, res["evidence"]
    assert "consola" in res["evidence"]
