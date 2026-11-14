"""Cerrar Discord nombraba 8 procesos y mentía sobre lo que había pasado.

Incidente del 2026-10-02. Bruno pidió "cierra discord" y leyó:

    Le pedí a Discord.exe, Discord.exe, Discord.exe, Discord.exe, Discord.exe,
    DiscordSystemHelper.exe, Discord.exe, DiscordSystemHelper.exe que cierre y
    sigue abierto — puede estar esperando que guardes o descartes los cambios.

Medido: exactamente 8 procesos matchean "discord", y UNO SOLO tiene ventana.

Tres defectos:

1. **El mensaje listaba procesos.** `res.matched` trae un nombre por PID, sin
   dedup. El usuario dijo "discord", no "Discord.exe".

2. **`CloseOutcome.BACKGROUND` era inalcanzable.** Estaba declarada y el
   formateador la consumía, pero `platform.py` no la devolvía NUNCA. Discord,
   Spotify y Teams cierran su ventana y se quedan en la bandeja — que es
   exactamente lo que se les pidió — y caían en PENDING.

3. **PENDING afirmaba un diálogo de guardado como si fuera un hecho.** Que
   quede una ventana significa que el cierre quedó incompleto; puede ser un
   diálogo, otra ventana de la app, o un updater. Nombrar la posibilidad sí;
   afirmarla no.

El criterio nuevo: **seguir VIVO no es seguir ABIERTO.** La pregunta es por la
ventana, no por el proceso.
"""
import sys
import types

import pytest

from core import platform as plat
from core.platform import CloseOutcome, request_close


class FakeProc:
    def __init__(self, pid, name):
        self.pid = pid
        self.info = {"name": name}
        self.killed = False

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_psutil(monkeypatch):
    """psutil completo mockeado: acá no muere ningún proceso real."""
    # `windowed`  = PIDs que tienen ventana AHORA
    # `tray`      = PIDs que, al recibir WM_CLOSE, ESCONDEN su ventana y siguen
    #               vivos (Discord, Spotify, Teams). Modelar esto importa: el
    #               bug era justamente no distinguir "se escondió" de "me ignoró".
    state = types.SimpleNamespace(procs=[], gone=[], windowed=set(), tray=set())

    mod = types.ModuleType("psutil")
    mod.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    mod.AccessDenied = type("AccessDenied", (Exception,), {})
    mod.process_iter = lambda fields=None: list(state.procs)
    mod.pid_exists = lambda pid: any(p.pid == pid for p in state.procs)

    def wait_procs(procs, timeout=None, callback=None):
        gone = [p for p in procs if p in state.gone]
        alive = [p for p in procs if p not in state.gone]
        return gone, alive

    mod.wait_procs = wait_procs
    monkeypatch.setitem(sys.modules, "psutil", mod)

    def close_windows(pid):
        if pid not in state.windowed:
            return False          # no hay a quién pedirle
        if pid in state.tray:
            state.windowed.discard(pid)   # la app esconde su ventana
        return True

    monkeypatch.setattr(plat, "_close_windows_of_pid", close_windows)
    monkeypatch.setattr(plat, "_pid_has_visible_window",
                        lambda pid: pid in state.windowed)
    return state


def _discord_tree():
    """La forma REAL medida en la máquina de Bruno: 8 procesos, 1 ventana."""
    return [
        FakeProc(17800, "Discord.exe"),
        FakeProc(17920, "Discord.exe"),
        FakeProc(18104, "Discord.exe"),
        FakeProc(18132, "Discord.exe"),
        FakeProc(18652, "Discord.exe"),
        FakeProc(20296, "DiscordSystemHelper.exe"),
        FakeProc(21948, "Discord.exe"),
        FakeProc(22464, "DiscordSystemHelper.exe"),
    ]


# ── Una app que se va a la bandeja CERRÓ su ventana ─────────────────────
def test_an_app_that_parks_in_the_tray_is_not_pending(fake_psutil):
    """La rama que nunca se construía. Discord hace justo esto."""
    fake_psutil.procs = _discord_tree()
    fake_psutil.windowed = {17800}       # sólo el principal tiene ventana
    fake_psutil.tray = {17800}           # al cerrarse, la esconde
    fake_psutil.gone = []                # ningún proceso muere: se va al tray

    res = request_close("discord")

    assert res.outcome is CloseOutcome.BACKGROUND, (
        "cerró su ventana y siguió en la bandeja: eso NO es 'sigue abierto'"
    )
    assert res.ok


def test_a_window_that_stays_is_pending(fake_psutil):
    """Contraparte legítima: si la ventana sigue ahí, el cierre quedó a medias."""
    fake_psutil.procs = [FakeProc(1, "WINWORD.EXE")]
    fake_psutil.windowed = {1}
    fake_psutil.gone = []

    # La ventana sigue después del WM_CLOSE → quedó algo abierto.
    res = request_close("word")

    assert res.outcome is CloseOutcome.PENDING
    assert res.still_alive == ("WINWORD.EXE",)


def test_everything_gone_is_closed(fake_psutil):
    fake_psutil.procs = [p := FakeProc(1, "notepad.exe")]
    fake_psutil.windowed = {1}
    fake_psutil.gone = [p]

    assert request_close("notepad").outcome is CloseOutcome.CLOSED


def test_no_window_at_all_is_not_a_failed_close(fake_psutil):
    """Ya estaba en la bandeja antes de pedirle nada."""
    fake_psutil.procs = _discord_tree()
    fake_psutil.windowed = set()

    res = request_close("discord")

    assert res.outcome is CloseOutcome.NO_WINDOW
    assert all(not p.killed for p in fake_psutil.procs)


# ── El mensaje nombra la app, no los procesos ───────────────────────────
@pytest.fixture
def controller():
    """AppController SIN tocar el browser.

    `AppController()` instancia BrowserConfig, que en Windows hace CDP attach
    al Edge REAL de Bruno y lo puede dejar zombie. Nunca se construye entero.
    """
    from modules.automation.app_controller import AppController
    return AppController.__new__(AppController)


def test_the_message_names_the_app_not_eight_processes(fake_psutil, controller):
    fake_psutil.procs = _discord_tree()
    fake_psutil.windowed = {17800}
    fake_psutil.tray = {17800}
    fake_psutil.gone = []

    msg = controller.close_app("discord")

    assert "Discord.exe" not in msg, f"sigue listando procesos: {msg!r}"
    assert msg.count("Discord") == 1, f"nombra la app mas de una vez: {msg!r}"
    assert "bandeja" in msg


def test_pending_does_not_assert_a_save_dialog_as_fact(fake_psutil, controller):
    """Que quede una ventana NO prueba que haya un diálogo de guardado."""
    fake_psutil.procs = [FakeProc(1, "WINWORD.EXE")]
    fake_psutil.windowed = {1}
    fake_psutil.gone = []

    msg = controller.close_app("word")

    assert "WINWORD" not in msg
    assert "Puede estar" in msg, "lo afirma en vez de nombrarlo como posibilidad"


def test_an_unknown_app_keeps_the_users_own_word(fake_psutil, controller):
    fake_psutil.procs = [FakeProc(1, "cosarara.exe")]
    fake_psutil.windowed = {1}
    fake_psutil.gone = []

    assert "cosarara" in controller.close_app("cosarara")


# ── El atajo por clase de ventana era un bug mío del mismo día ──────────
def test_window_class_is_never_used_as_a_shortcut(monkeypatch):
    """`FindWindowW("Chrome_WidgetWin_1")` devuelve el overlay de Búsqueda.

    Esa clase la comparten Edge, Discord, CurseForge, los Widgets y el menú
    Inicio de Windows. Usarla como atajo contestaba "sí, Discord tiene
    ventana" mirando una ventana de Edge.
    """
    monkeypatch.setattr(plat, "_pid_has_visible_window", lambda pid: False)

    mod = types.ModuleType("psutil")
    mod.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    mod.AccessDenied = type("AccessDenied", (Exception,), {})
    mod.process_iter = lambda f=None: [FakeProc(1, "Discord.exe")]
    monkeypatch.setitem(sys.modules, "psutil", mod)

    assert plat.has_visible_window("Discord", "Chrome_WidgetWin_1") is False, (
        "contesto que si por una ventana que no es de Discord"
    )
