"""`core.platform.kill_process` — el ejecutor que de verdad mata procesos.

No había ni un test. Y no se puede probar en vivo: si la protección estuviera
rota, `kill_process("python")` se llevaría el backend de Zephyro (que ES un
proceso python) y el propio pytest. Así que psutil va mockeado entero — acá no
muere ningún proceso real.

Dos cambios del 2026-10-01 que estos tests fijan:

1. **Ya NO matchea contra el cmdline** salvo que se pida explícito. Antes hacía
   `name in pname or name in cmdline`, así que `close_app("edge")` alcanzaba
   cualquier proceso cuya línea de comando mencionara "edge" — incluido el
   perfil `…\\EdgeAutomation` de Zephyro — y `close_app("word")` cualquier
   terminal con "word" en su comando. El guardrail de `action_risk_rules`
   clasifica mirando el NOMBRE del target, así que estructuralmente no podía
   ver lo que iba a morir: gate y ejecutor en universos distintos.

2. **Pide WM_CLOSE y espera antes de matar.** `proc.kill()` es
   TerminateProcess: el documento sin guardar se va sin diálogo de
   recuperación.
"""
import sys
import types

import pytest

from core import platform as plat


class FakeProc:
    def __init__(self, pid, name, cmdline=(), has_window=False):
        self.pid = pid
        self.info = {"name": name, "cmdline": list(cmdline)}
        self.has_window = has_window
        self.killed = False

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_psutil(monkeypatch):
    """Reemplaza el módulo psutil que `kill_process` importa adentro.

    El import es local a la función, así que se parchea en `sys.modules`.
    """
    state = types.SimpleNamespace(procs=[], waited=None, gone=[])

    mod = types.ModuleType("psutil")
    mod.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    mod.AccessDenied = type("AccessDenied", (Exception,), {})
    mod.process_iter = lambda fields=None: list(state.procs)

    def wait_procs(procs, timeout=None, callback=None):
        state.waited = (list(procs), timeout)
        gone = [p for p in procs if p in state.gone]
        alive = [p for p in procs if p not in state.gone]
        return gone, alive

    mod.wait_procs = wait_procs
    monkeypatch.setitem(sys.modules, "psutil", mod)
    # Por default ninguna ventana contesta: así se ejercita el camino de kill.
    monkeypatch.setattr(plat, "_close_windows_of_pid", lambda pid: False)
    return state


# ── Procesos protegidos: defensa en profundidad del lado del ejecutor ───
@pytest.mark.parametrize("protected", sorted(plat.PROTECTED_PROCESSES))
def test_protected_processes_are_never_killed(fake_psutil, protected):
    victim = FakeProc(1, f"{protected}.exe")
    fake_psutil.procs = [victim]

    assert plat.kill_process(protected) is False
    assert not victim.killed, (
        f"{protected} esta protegido: matarlo tumba a Zephyro o al escritorio"
    )


def test_a_protected_process_does_not_shield_the_others(fake_psutil):
    """Un protegido en la lista de matches no debe abortar el resto."""
    protected = FakeProc(1, "python.exe")
    ordinary = FakeProc(2, "pythonish-app.exe")
    fake_psutil.procs = [protected, ordinary]

    assert plat.kill_process("python") is True
    assert not protected.killed
    assert ordinary.killed


# ── El cmdline ya no alcanza ────────────────────────────────────────────
def test_cmdline_is_not_matched_by_default(fake_psutil):
    """El caso real: close_app("edge") alcanzaba el perfil de Zephyro."""
    zephyro_profile = FakeProc(
        1, "msedge_automation_helper.exe",
        cmdline=[r"--user-data-dir=C:\Users\user\Zephyro\EdgeAutomation"],
    )
    fake_psutil.procs = [zephyro_profile]

    # El nombre no contiene "chrome"; antes el cmdline lo delataba igual.
    assert plat.kill_process("chrome") is False
    assert not zephyro_profile.killed


def test_cmdline_matching_is_opt_in(fake_psutil):
    p = FakeProc(1, "launcher.exe", cmdline=["--profile", "mi-chrome-profile"])
    fake_psutil.procs = [p]

    assert plat.kill_process("chrome") is False
    assert plat.kill_process("chrome", match_cmdline=True) is True
    assert p.killed


def test_name_still_matches_as_substring(fake_psutil):
    """"word" tiene que alcanzar WINWORD.EXE: es lo que el usuario nombra."""
    p = FakeProc(1, "WINWORD.EXE")
    fake_psutil.procs = [p]

    assert plat.kill_process("word") is True
    assert p.killed


# ── Cierre graceful antes de matar ──────────────────────────────────────
def test_windows_are_asked_to_close_before_killing(fake_psutil, monkeypatch):
    asked = []
    monkeypatch.setattr(
        plat, "_close_windows_of_pid", lambda pid: (asked.append(pid), True)[1]
    )
    p = FakeProc(1, "spotify.exe")
    fake_psutil.procs = [p]
    fake_psutil.gone = [p]  # cerro sola tras el WM_CLOSE

    assert plat.kill_process("spotify") is True
    assert asked == [1], "no le pidio cerrar antes de matar"
    assert not p.killed, "la mato igual aunque habia cerrado sola"


def test_a_process_that_ignores_wm_close_is_killed(fake_psutil, monkeypatch):
    monkeypatch.setattr(plat, "_close_windows_of_pid", lambda pid: True)
    p = FakeProc(1, "spotify.exe")
    fake_psutil.procs = [p]
    fake_psutil.gone = []  # no cerro en el timeout

    assert plat.kill_process("spotify") is True
    assert p.killed, "si ignora WM_CLOSE hay que matarlo"


def test_graceful_can_be_disabled(fake_psutil, monkeypatch):
    asked = []
    monkeypatch.setattr(
        plat, "_close_windows_of_pid", lambda pid: (asked.append(pid), True)[1]
    )
    p = FakeProc(1, "spotify.exe")
    fake_psutil.procs = [p]

    assert plat.kill_process("spotify", graceful=False) is True
    assert asked == [], "con graceful=False no debe pedir WM_CLOSE"
    assert p.killed


def test_graceful_timeout_is_passed_through(fake_psutil, monkeypatch):
    monkeypatch.setattr(plat, "_close_windows_of_pid", lambda pid: True)
    p = FakeProc(1, "spotify.exe")
    fake_psutil.procs = [p]

    plat.kill_process("spotify", graceful_timeout=1.5)

    assert fake_psutil.waited is not None, "nunca esperó a que cerraran"
    assert fake_psutil.waited[1] == 1.5


# ── Entradas degeneradas ────────────────────────────────────────────────
@pytest.mark.parametrize("target", ["", "   ", ".exe"])
def test_empty_target_kills_nothing(fake_psutil, target):
    """Un target vacío matcheaba TODO con el `in` de antes."""
    p = FakeProc(1, "cualquier-cosa.exe")
    fake_psutil.procs = [p]

    assert plat.kill_process(target) is False
    assert not p.killed


def test_no_match_returns_false(fake_psutil):
    fake_psutil.procs = [FakeProc(1, "spotify.exe")]
    assert plat.kill_process("inkscape") is False


# ── Cerrar NO es matar (Units 4 y 5) ────────────────────────────────────
# El camino viejo mandaba WM_CLOSE, esperaba 5 s y mataba lo que siguiera
# vivo. Si el WM_CLOSE abria el dialogo "¿guardar cambios?" y el usuario no
# contestaba a tiempo, le destruiamos justo lo que el dialogo protegia — peor
# que no preguntar, porque el usuario ve un dialogo y cree que decide.
# `request_close` nunca mata; `force_terminate` requiere intencion explicita.
from core.platform import (  # noqa: E402
    CloseOutcome,
    force_terminate,
    request_close,
)


def test_request_close_never_kills(fake_psutil, monkeypatch):
    monkeypatch.setattr(plat, "_close_windows_of_pid", lambda pid: True)
    # La ventana SIGUE ahí después del WM_CLOSE: eso es lo que distingue
    # "quedó algo abierto" de "se fue a la bandeja" (CloseOutcome.BACKGROUND).
    # Sin esto el escenario sería el de una app que cierra su ventana y vive,
    # que es otro caso y tiene su propio test.
    monkeypatch.setattr(plat, "_pid_has_visible_window", lambda pid: True)
    p = FakeProc(1, "WINWORD.EXE")
    fake_psutil.procs = [p]
    fake_psutil.gone = []  # no cerro: hay un dialogo esperando

    res = request_close("word")

    assert not p.killed, "mato un proceso que podia tener un dialogo abierto"
    assert res.outcome is CloseOutcome.PENDING
    assert res.still_alive == ("WINWORD.EXE",)


def test_request_close_reports_when_it_did_close(fake_psutil, monkeypatch):
    monkeypatch.setattr(plat, "_close_windows_of_pid", lambda pid: True)
    p = FakeProc(1, "notepad.exe")
    fake_psutil.procs = [p]
    fake_psutil.gone = [p]

    res = request_close("notepad")

    assert res.outcome is CloseOutcome.CLOSED
    assert res.ok


def test_request_close_without_a_window_does_not_kill(fake_psutil, monkeypatch):
    """Proceso de bandeja: no hay a quien pedirle, y no se mata por eso."""
    monkeypatch.setattr(plat, "_close_windows_of_pid", lambda pid: False)
    p = FakeProc(1, "spotify.exe")
    fake_psutil.procs = [p]

    res = request_close("spotify")

    assert res.outcome is CloseOutcome.NO_WINDOW
    assert not p.killed


def test_force_terminate_kills_without_asking(fake_psutil, monkeypatch):
    asked = []
    monkeypatch.setattr(
        plat, "_close_windows_of_pid", lambda pid: (asked.append(pid), True)[1]
    )
    p = FakeProc(1, "spotify.exe")
    fake_psutil.procs = [p]

    res = force_terminate("spotify")

    assert p.killed
    assert res.outcome is CloseOutcome.CLOSED
    assert asked == [], "force_terminate no deberia mandar WM_CLOSE"


@pytest.mark.parametrize("fn", [request_close, force_terminate])
def test_a_protected_process_is_reported_not_silently_missing(fake_psutil, fn):
    """Antes decia "no encontre el proceso": el gate pedia confirmacion, el
    usuario confirmaba, y recibia una mentira."""
    p = FakeProc(1, "explorer.exe")
    fake_psutil.procs = [p]

    res = fn("explorer")

    assert res.outcome is CloseOutcome.PROTECTED
    assert res.protected == ("explorer.exe",)
    assert not p.killed


@pytest.mark.parametrize("fn", [request_close, force_terminate])
def test_nothing_matched_is_not_found(fake_psutil, fn):
    fake_psutil.procs = [FakeProc(1, "spotify.exe")]
    assert fn("inkscape").outcome is CloseOutcome.NOT_FOUND


def test_resolution_is_shared_between_both_operations(fake_psutil):
    """Una sola fuente de "que proceso es 'word'".

    El gate y el ejecutor discrepaban: el gate comparaba por token y
    `kill_process` por substring, asi que `close "pad"` salia REVERSIBLE
    mientras el ejecutor alcanzaba notepad.exe Y wordpad.exe.
    """
    a, b = FakeProc(1, "notepad.exe"), FakeProc(2, "wordpad.exe")
    fake_psutil.procs = [a, b]

    victims, protected = plat._match_processes("pad")

    assert {p.pid for p in victims} == {1, 2}
    assert protected == []
