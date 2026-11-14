"""
Detección de plataforma y utilidades cross-platform.

Centraliza toda la lógica OS-specific para que el resto del código
importe desde aquí en vez de duplicar sys.platform checks.
"""
import os
import shutil
import struct
import sys
import subprocess

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

# ── Procesos que NUNCA se matan ──────────────────────────────────────────
# Matarlos tumba a Zephyro, al escritorio o a la sesión. Vive acá, en el
# ejecutor, porque es el único lugar que ve el nombre REAL del proceso que
# matcheó — `action_risk_rules.classify_action` solo ve el target que escribió
# el usuario, así que no puede saber qué va a morir. Esa diferencia de
# universos es la que dejaba pasar `close_app("edge")` matando el perfil de
# automatización de Zephyro. `action_risk_rules` importa esta lista.
PROTECTED_PROCESSES = frozenset({
    "explorer", "python", "pythonw", "node", "uvicorn", "zephyro", "jarvis",
    "code", "powershell", "pwsh", "cmd", "windowsterminal", "terminal",
    "ollama", "svchost", "winlogon", "csrss", "system",
})
IS_LINUX = sys.platform == "linux"


def popen_detach_kwargs() -> dict:
    """Kwargs para subprocess.Popen que desacoplan el proceso hijo.

    SOLO sirve para apps GUI. Una app de CONSOLA lanzada con
    DETACHED_PROCESS no hereda la consola del padre ni recibe una propia:
    arranca sin stdin y termina al instante. Para lanzar algo que el usuario
    tiene que VER, usar `popen_launch_kwargs`.
    """
    if IS_WINDOWS:
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS}
    return {"start_new_session": True}


# ── Consola vs GUI: no es un detalle de estilo, decide si la app vive ─────
# `powershell`, `cmd` y `wt` son aplicaciones de CONSOLA. Lanzadas con
# DETACHED_PROCESS se quedan sin consola y mueren antes de que el usuario vea
# nada; el launcher igual reportaba "✓". Medido el 2026-10-02:
#
#   DETACHED_PROCESS   + DEVNULL  -> pid=7584  vivo=False  ventanas=0
#   CREATE_NEW_CONSOLE (sin pipe) -> pid=1644  vivo=True   ventanas=1
#
# La clase se cierra leyendo el subsistema declarado en la cabecera PE en vez
# de mantener una lista de nombres: así cubre también las apps de consola que
# todavía no están en ninguna whitelist.
_SUBSYSTEM_GUI = 2
_SUBSYSTEM_CONSOLE = 3


@lru_cache(maxsize=256)
def _pe_subsystem(path: str) -> Optional[int]:
    """Subsistema declarado en la cabecera PE. None si no es un PE legible."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(0x400)
        if head[:2] != b"MZ":
            return None
        pe = struct.unpack_from("<I", head, 0x3C)[0]
        if head[pe:pe + 4] != b"PE\0\0":
            return None
        # OptionalHeader arranca en pe+24; Subsystem está 68 bytes adentro.
        return struct.unpack_from("<H", head, pe + 24 + 68)[0]
    except Exception:
        return None


def is_console_app(executable: str) -> bool:
    """¿`executable` necesita una consola propia para ser usable?

    Resuelve por PATH igual que `subprocess.Popen`, y la respuesta sale de la
    cabecera del binario, no de una lista de nombres.

    Un shim `.cmd` (el `code.CMD` de VS Code, por ejemplo) NO cuenta: no es un
    PE, no es lo que el usuario ve, y lanza otra cosa. Medido que sobrevive
    desacoplado, así que darle una consola solo le haría parpadear una ventana
    negra en la cara al usuario.
    """
    if not IS_WINDOWS or not executable:
        return False
    path = executable if os.path.isabs(executable) else shutil.which(executable)
    if not path:
        return False
    return _pe_subsystem(path) == _SUBSYSTEM_CONSOLE


def popen_launch_kwargs(executable: str) -> dict:
    """Kwargs de Popen para abrir una app PARA EL USUARIO.

    GUI → desacoplada y sin consola. Consola → consola nueva y visible, y sin
    redirigir los streams: una consola sin stdout no le sirve a nadie.
    """
    if not IS_WINDOWS:
        return {
            "start_new_session": True,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NEW_CONSOLE = 0x00000010
    if is_console_app(executable):
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE}
    return {
        **popen_detach_kwargs(),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }


def is_process_running(name: str) -> bool:
    """Verifica si un proceso con ese nombre está corriendo (cross-platform)."""
    import psutil
    name_lower = name.lower()
    for proc in psutil.process_iter(['name']):
        try:
            pname = proc.info['name']
            if pname and name_lower in pname.lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def _close_windows_of_pid(pid: int) -> bool:
    """Manda WM_CLOSE a las ventanas visibles de un PID. True si mandó alguno.

    Es el equivalente a que el usuario haga click en la X: la app corre su
    `beforeunload` / "¿guardar cambios?". `psutil.kill()` no hace nada de
    eso — en Windows `terminate()` y `kill()` son los dos TerminateProcess,
    que se lleva el documento sin diálogo de recuperación.
    """
    if not IS_WINDOWS:
        return False
    import ctypes
    from ctypes import wintypes

    WM_CLOSE = 0x0010
    try:
        user32 = ctypes.windll.user32
        sent = [False]
        target_pid = int(pid)

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def enum_callback(hwnd, lparam):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                win_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
                if win_pid.value == target_pid:
                    # PostMessage, no SendMessage: SendMessage BLOQUEA hasta que
                    # la app conteste, y si abre un diálogo "¿guardar?" no
                    # contesta nunca. Un worker de uvicorn colgado ahí es el
                    # incidente del 2026-10-01 otra vez.
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    sent[0] = True
            return True

        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return sent[0]
    except Exception:
        return False


class CloseOutcome(str, Enum):
    """Qué pasó realmente al pedir cerrar algo.

    Un `bool` no podía expresar "le pedí cerrar y quedó esperando que guardes",
    que es justo el estado que importa.
    """
    CLOSED = "closed"                      # cerró
    PENDING = "close_requested_pending"    # le pedimos y sigue abierto
    BACKGROUND = "still_running_background"  # cerró su ventana, sigue en el tray
    NO_WINDOW = "no_window"                # no tiene ventana a la que pedirle
    PROTECTED = "protected"                # está en PROTECTED_PROCESSES
    NOT_FOUND = "not_found"
    DENIED = "access_denied"


@dataclass(frozen=True)
class CloseResult:
    outcome: CloseOutcome
    matched: tuple[str, ...] = ()      # nombres reales de proceso que matchearon
    still_alive: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()    # los que se saltearon por protegidos

    @property
    def ok(self) -> bool:
        return self.outcome in (CloseOutcome.CLOSED, CloseOutcome.BACKGROUND)


def _match_processes(name: str, *, match_cmdline: bool = False) -> tuple[list, list]:
    """Resuelve el target a procesos REALES. Devuelve (víctimas, protegidos).

    Fuente única de la resolución: tanto pedir el cierre como forzarlo usan
    esta, así que no pueden discrepar sobre qué proceso es "word".
    """
    import psutil

    name_lower = name.strip().lower().removesuffix(".exe")
    if not name_lower:
        return [], []

    fields = ['name', 'cmdline'] if match_cmdline else ['name']
    victims: list = []
    protected: list = []
    for proc in psutil.process_iter(fields):
        try:
            pname = (proc.info['name'] or "").lower()
            hit = name_lower in pname
            if not hit and match_cmdline:
                cmdline = " ".join(proc.info['cmdline'] or []).lower()
                hit = name_lower in cmdline
            if not hit:
                continue
            if pname.removesuffix(".exe") in PROTECTED_PROCESSES:
                protected.append(proc)
                continue
            victims.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return victims, protected


def request_close(name: str, *, match_cmdline: bool = False,
                  wait: float = 3.0) -> CloseResult:
    """Le PIDE a la app que cierre (WM_CLOSE). Nunca mata.

    Cerrar no es matar, y el sistema no tenía esa distinción. El camino viejo
    mandaba WM_CLOSE, esperaba 5 s y mataba lo que siguiera vivo — o sea que si
    el WM_CLOSE abría el diálogo "¿guardar cambios?" y el usuario no contestaba
    a tiempo, le destruíamos justo lo que el diálogo protegía. Peor que no
    preguntar, porque el usuario ve un diálogo y cree que decide.

    Forzar la terminación es `force_terminate`, y requiere una orden aparte.
    """
    import psutil

    victims, protected = _match_processes(name, match_cmdline=match_cmdline)
    if not victims:
        if protected:
            return CloseResult(
                CloseOutcome.PROTECTED,
                protected=tuple(_pname(p) for p in protected),
            )
        return CloseResult(CloseOutcome.NOT_FOUND)

    asked = [p for p in victims if _close_windows_of_pid(p.pid)]
    if not asked:
        # Sin ventana visible no hay a quién pedirle: típico de un proceso de
        # tray o de un servicio. No se mata por eso.
        return CloseResult(
            CloseOutcome.NO_WINDOW,
            matched=tuple(_pname(p) for p in victims),
            still_alive=tuple(_pname(p) for p in victims),
        )

    gone, alive = psutil.wait_procs(asked, timeout=wait)
    matched = tuple(_pname(p) for p in victims)
    if not alive:
        return CloseResult(CloseOutcome.CLOSED, matched=matched)

    # Seguir VIVO no es seguir ABIERTO. Discord, Spotify y Teams cierran su
    # ventana y se quedan en la bandeja: eso es exactamente lo que el usuario
    # pidió. Mirando solo el proceso, los tres caían en PENDING y recibían
    # "puede estar esperando que guardes los cambios" — falso, y encima
    # alarmante. Por eso la pregunta es por la VENTANA.
    #
    # Un diálogo "¿guardar cambios?" ES una ventana visible con título del
    # mismo PID, así que ese caso sigue dando PENDING, que es donde esa
    # frase es verdad.
    still_open = [p for p in alive if _pid_has_visible_window(p.pid)]
    if not still_open:
        return CloseResult(
            CloseOutcome.BACKGROUND,
            matched=matched,
            still_alive=tuple(_pname(p) for p in alive),
        )
    return CloseResult(
        CloseOutcome.PENDING,
        matched=matched,
        still_alive=tuple(_pname(p) for p in still_open),
    )


def _pname(proc) -> str:
    try:
        return proc.info.get('name') or f"pid {proc.pid}"
    except Exception:
        return f"pid {proc.pid}"


def force_terminate(name: str, *, match_cmdline: bool = False) -> CloseResult:
    """Mata el proceso (TerminateProcess). Requiere intención explícita.

    No manda WM_CLOSE ni espera: si se llega acá es porque el usuario ya pidió
    forzar, y eso ya pasó por una confirmación HIGH.
    """
    import psutil

    victims, protected = _match_processes(name, match_cmdline=match_cmdline)
    if not victims:
        if protected:
            return CloseResult(
                CloseOutcome.PROTECTED,
                protected=tuple(_pname(p) for p in protected),
            )
        return CloseResult(CloseOutcome.NOT_FOUND)

    killed: list[str] = []
    denied: list[str] = []
    for proc in victims:
        label = _pname(proc)
        try:
            proc.kill()
            killed.append(label)
        except psutil.NoSuchProcess:
            killed.append(label)
        except psutil.AccessDenied:
            denied.append(label)
    if not killed:
        return CloseResult(CloseOutcome.DENIED, matched=tuple(denied))
    return CloseResult(CloseOutcome.CLOSED, matched=tuple(killed))


def kill_process(name: str, *, match_cmdline: bool = False,
                 graceful: bool = True, graceful_timeout: float = 5.0) -> bool:
    """DEPRECADO — usá `request_close` o `force_terminate`.

    Mezcla las dos operaciones: pide cerrar y, si no cerró en
    `graceful_timeout`, mata. Eso destruye el trabajo que el diálogo "¿guardar
    cambios?" estaba protegiendo, justo cuando el usuario tarda en contestar.
    Se mantiene por compatibilidad; no agregar llamadores nuevos.

    Cierra procesos que coincidan con el nombre. True si cerró alguno.

    Dos cambios del 2026-10-01, los dos por el mismo motivo — esta función
    alcanzaba más de lo que el usuario nombró, y lo hacía sin piedad:

    1. **Ya NO matchea contra el cmdline** salvo que se pida explícito. Antes
       hacía `name in pname or name in cmdline`, o sea que `close_app("edge")`
       alcanzaba cualquier proceso cuya línea de comando mencionara "edge"
       (incluido el perfil `…\\EdgeAutomation` de Zephyro), y
       `close_app("word")` cualquier terminal que tuviera "word" en su
       comando. El guardrail de `action_risk_rules` clasifica mirando solo el
       NOMBRE, así que estructuralmente no podía ver lo que iba a morir.

    2. **Intenta WM_CLOSE antes de matar.** `proc.kill()` es TerminateProcess:
       el documento sin guardar se va sin diálogo de recuperación.

    Nunca toca los de `PROTECTED_PROCESSES`. El guardrail ya debería haber
    pedido confirmación para esos; esto es defensa en profundidad, para que un
    camino que se saltee el guardrail no pueda tumbar a Zephyro ni al
    escritorio.
    """
    import psutil

    name_lower = name.strip().lower().removesuffix(".exe")
    if not name_lower:
        return False

    fields = ['name', 'cmdline'] if match_cmdline else ['name']
    victims: list = []
    for proc in psutil.process_iter(fields):
        try:
            pname = (proc.info['name'] or "").lower()
            hit = name_lower in pname
            if not hit and match_cmdline:
                cmdline = " ".join(proc.info['cmdline'] or []).lower()
                hit = name_lower in cmdline
            if not hit:
                continue
            # Defensa en profundidad: el nombre REAL del proceso, no el target.
            stem = pname.removesuffix(".exe")
            if stem in PROTECTED_PROCESSES:
                continue
            victims.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if not victims:
        return False

    if graceful:
        asked = [p for p in victims if _close_windows_of_pid(p.pid)]
        if asked:
            gone, alive = psutil.wait_procs(asked, timeout=graceful_timeout)
            # Las que cerraron solas ya no son víctimas de kill().
            closed = {p.pid for p in gone}
            victims = [p for p in victims if p.pid not in closed]
            if not victims:
                return True

    killed = False
    for proc in victims:
        try:
            proc.kill()
            killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed


def open_url_default(url: str):
    """Abre una URL en el navegador predeterminado del sistema."""
    if IS_WINDOWS:
        os.startfile(url)
    else:
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )


def foreground_process_name() -> Optional[str]:
    """Nombre del ejecutable dueño de la ventana en foco, en minúsculas. None si no se sabe.

    Resuelve por PID, nunca por título — misma regla que `_focus_window_windows`, que
    aprendió a los golpes que `Chrome_WidgetWin_1` y los títulos de ventana no dicen
    de quién es la ventana (una pestaña del navegador titulada "… Discord …" matcheaba
    "discord"). Para decidir si el usuario ESTÁ jugando, el título no alcanza: el dueño
    del proceso sí.

    Solo lecturas (`GetForegroundWindow` + `GetWindowThreadProcessId`): NO toca
    `AttachThreadInput` ni `ShowWindow`, que son los que dejaron la PC sin responder
    a los clicks el 2026-10-01.
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import psutil

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        return (psutil.Process(pid.value).name() or "").lower() or None
    except Exception:
        return None


def focus_window(process_name: str, window_class: str = None, window_title: str = None) -> bool:
    """
    Trae la ventana de una aplicación al frente.
    Cross-platform: Windows usa ctypes/win32gui, Linux usa xdotool o wmctrl.

    Args:
        process_name: Nombre del proceso (ej. "Discord")
        window_class: Clase de ventana WM_CLASS (ej. "discord") — opcional
        window_title: Patrón parcial del título de ventana — opcional
    Returns:
        True si se pudo activar la ventana, False en caso contrario.
    """
    if IS_WINDOWS:
        return _focus_window_windows(process_name, window_class, window_title)
    return _focus_window_linux(process_name, window_class, window_title)


def _activate_hwnd_windows(user32, hwnd) -> bool:
    """Trae `hwnd` al frente DE VERDAD y confirma que quedó ahí.

    `SetForegroundWindow` solo funciona si el proceso que llama ya tiene el foco
    o recibió input reciente del usuario: desde un servicio en background
    (uvicorn) Windows lo bloquea y devuelve 0 **sin error**. Por eso acá se
    verifica con `GetForegroundWindow` y, si hizo falta, se escalan dos
    workarounds estándar antes de reportar el fracaso.
    """
    import ctypes
    from ctypes import wintypes

    SW_RESTORE, SW_SHOW = 9, 5

    def _is_front() -> bool:
        return bool(user32.GetForegroundWindow() == hwnd)

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)
    user32.SetForegroundWindow(hwnd)
    if _is_front():
        return True

    # NO se fuerza el foreground más allá de esto. Acá había dos escalaciones y
    # las dos se quitaron el 2026-10-01, después de que un "abrí discord" (que
    # Discord ya estuviera abierto → camino de focus) dejara la PC entera sin
    # responder a los clicks:
    #
    #   1. AttachThreadInput(fg_tid, target_tid, True): une las colas de input de
    #      dos threads GUI ajenos, llamado desde un thread de uvicorn que no
    #      bombea mensajes. Si el thread destino está ocupado —Discord es Electron
    #      y venía con carga— la llamada de foreground se bloquea CON LAS COLAS
    #      YA UNIDAS, y el input de esas ventanas se serializa detrás del que está
    #      trabado. Es la causa clásica de "la PC no registra los clicks". El
    #      try/finally desadjunta, pero recién cuando la llamada vuelve.
    #   2. ShowWindow(MINIMIZE) + RESTORE sobre una ventana ajena: le arrebata la
    #      ventana al usuario para ganar una pelea de foco que Windows ya decidió.
    #
    # Windows bloquea el robo de foreground a propósito. Pelearlo desde un
    # servicio en background es el lado equivocado del trade-off: el costo es la
    # sesión del usuario. Si no se pudo, se pide atención por la barra de tareas
    # (que es el mecanismo previsto) y se reporta el fracaso con honestidad.
    try:
        _flash_taskbar(user32, hwnd)
    except Exception:
        pass
    return _is_front()


def _flash_taskbar(user32, hwnd) -> None:
    """Parpadea el botón de la barra de tareas: la forma sancionada por Windows
    de pedir atención cuando no se puede (ni se debe) robar el foreground."""
    import ctypes
    from ctypes import wintypes

    class _FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    FLASHW_ALL, FLASHW_TIMERNOFG = 0x00000003, 0x0000000C
    info = _FLASHWINFO(
        cbSize=ctypes.sizeof(_FLASHWINFO), hwnd=hwnd,
        dwFlags=FLASHW_ALL | FLASHW_TIMERNOFG, uCount=3, dwTimeout=0,
    )
    user32.FlashWindowEx(ctypes.byref(info))


@dataclass(frozen=True)
class WindowCandidate:
    """Una ventana top-level del proceso objetivo, ya medida."""
    hwnd: int
    title: str = ""
    cls: str = ""
    area: int = 0
    visible: bool = False
    iconic: bool = False
    toolwindow: bool = False
    noactivate: bool = False


def _pick_window(candidates, window_class=None, window_title=None):
    """Elige la ventana PRINCIPAL entre las del proceso. Pura y testeable.

    Rankea en vez de filtrar duro, porque una app en la bandeja no tiene
    ninguna ventana "buena" y aun así hay que saber cuál es la principal para
    poder explicarlo. Clase y título son DESEMPATES entre ventanas que ya
    sabemos del proceso correcto — nunca selectores: `Chrome_WidgetWin_1` la
    comparten Edge, Discord, CurseForge, los Widgets y el menú Inicio.

    Devuelve `(candidata, activable)`. `activable` es False cuando la mejor
    ventana está oculta y no minimizada: mostrarla a la fuerza sería manotear
    una ventana que su dueño decidió esconder.
    """
    usable = [c for c in candidates if not c.noactivate]
    if not usable:
        return None, False

    def score(c: "WindowCandidate") -> int:
        s = 0
        if c.visible:
            s += 100
        if c.iconic:
            s += 60              # minimizada SÍ se puede restaurar
        if c.title:
            s += 20
        if c.area > 10_000:
            s += 20
        if c.toolwindow:
            # Penalización fuerte, no un bonus al revés: el "Discord Overlay"
            # es una toolwindow de PANTALLA COMPLETA, así que con un bonus
            # chico por tamaño le ganaba a la ventana principal cuando ésta
            # estaba colapsada en la bandeja. Una toolwindow nunca es la
            # ventana principal de una app. Se penaliza en vez de excluirse
            # porque hay apps que sólo tienen toolwindows.
            s -= 50
        if window_title and window_title.lower() in c.title.lower():
            s += 15
        if window_class and c.cls == window_class:
            s += 5
        return s

    best = max(usable, key=score)
    return best, bool(best.visible or best.iconic)


def _focus_window_windows(process_name: str, window_class: str = None, window_title: str = None) -> bool:
    """Trae al frente una ventana QUE SEA DE `process_name`.

    Reescrita el 2026-10-03. Antes había tres estrategias en orden, y las dos
    primeras **no miraban de quién era la ventana**:

      1. primera ventana cuyo TÍTULO contuviera el patrón. Sin chequeo de
         dueño ni de visibilidad: `"discord"` matcheaba una pestaña del
         navegador titulada "… Discord …".
      2. `FindWindowW(clase)`. Medido el 2026-10-02: con
         `Chrome_WidgetWin_1` —la clase que `APP_WINDOW_INFO` trae para
         Discord— eso devuelve el overlay de BÚSQUEDA de Windows 11
         (`msedgewebview2.exe` bajo `SearchHost.exe`). Esa clase la comparten
         Edge, Discord, CurseForge, los Widgets y el menú Inicio.

    Y después se hacía `ShowWindow(SW_SHOW)` sobre lo que viniera. Mostrar a
    la fuerza una ventana que su dueño mantiene OCULTA no es traer una app al
    frente: es manotear una ventana ajena.

    Ahora la resolución es SIEMPRE por PID. Clase y título bajan a ser
    criterios de desempate entre las ventanas del propio proceso, nunca
    selectores. Y si la mejor candidata está oculta, no se la muestra: se
    devuelve False y el llamador dice la verdad (la app está en la bandeja).

    Devuelve True solo si la ventana quedó realmente en foreground.
    """
    import ctypes
    from ctypes import wintypes

    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000

    try:
        user32 = ctypes.windll.user32
        import psutil

        proc_name_lower = (process_name or "").lower()
        if not proc_name_lower:
            return False
        target_pids = set()
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc_name_lower in (proc.info['name'] or '').lower():
                    target_pids.add(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if not target_pids:
            return False

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        candidates = []

        def enum_callback(hwnd, lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in target_pids:
                return True

            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value or ""

            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)

            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)

            visible = bool(user32.IsWindowVisible(hwnd))
            iconic = bool(user32.IsIconic(hwnd))

            candidates.append(WindowCandidate(
                hwnd=hwnd, title=title, cls=cls_buf.value, area=area,
                visible=visible, iconic=iconic,
                toolwindow=bool(ex & WS_EX_TOOLWINDOW),
                noactivate=bool(ex & WS_EX_NOACTIVATE),
            ))
            return True

        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)

        best, activable = _pick_window(candidates, window_class, window_title)
        if best is None or not activable:
            # Sin ventana mostrable: la app está en la bandeja. Se devuelve
            # False y el llamador lo dice; NO se fuerza a aparecer nada.
            return False

        return _activate_hwnd_windows(user32, best.hwnd)
    except Exception:
        return False


def wait_for_window(pid: int, timeout: float = 3.0, poll_interval: float = 0.2) -> bool:
    """
    Espera a que el proceso `pid` tenga una ventana top-level visible con título.
    Polling cada `poll_interval` segundos hasta `timeout`. Cross-platform.

    Returns:
        True si encontró ventana antes del timeout. False si timeout o el proceso
        murió. Para apps tray-only (sin ventana) siempre devuelve False.
    """
    import time

    if not pid or pid <= 0:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _pid_has_visible_window(pid):
            return True
        time.sleep(poll_interval)
    return False


def has_visible_window(process_name: str, window_class: str = None) -> bool:
    """¿Hay HOY una ventana visible de `process_name`?

    No es lo mismo que `is_process_running`, y confundirlos es un bug real:
    `explorer.exe` ES la shell de Windows, así que siempre está vivo. Preguntar
    por el proceso hacía que "abrí el explorador de archivos" contestara
    "ya está abierto" sin que hubiera una sola carpeta en pantalla. Lo mismo
    para cualquier app minimizada a la bandeja.

    **`window_class` ya NO se usa como atajo.** Lo tenía, y era un bug: medido
    el 2026-10-02, `FindWindowW("Chrome_WidgetWin_1", None)` devuelve el overlay
    de Búsqueda de Windows — esa clase la comparten Edge, Discord, CurseForge,
    los Widgets y el menú Inicio. O sea que el atajo contestaba "sí, Discord
    tiene ventana" mirando una ventana de Edge. El parámetro se acepta por
    compatibilidad con los llamadores y se ignora.

    La única respuesta válida cruza ventana contra PID del proceso.
    """
    if not process_name:
        return False

    try:
        import psutil
        name_lower = process_name.lower()
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if name_lower in (proc.info['name'] or '').lower():
                    if _pid_has_visible_window(proc.info['pid']):
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        # Sin psutil no se puede responder; que no bloquee el lanzamiento.
        return False
    return False


def _pid_has_visible_window(pid: int) -> bool:
    """Helper sincrónico: ¿este PID tiene al menos una ventana visible con título?"""
    if IS_WINDOWS:
        return _pid_has_visible_window_windows(pid)
    return _pid_has_visible_window_linux(pid)


def _pid_has_visible_window_windows(pid: int) -> bool:
    """Enumera ventanas top-level y matchea por GetWindowThreadProcessId."""
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.windll.user32
        found = [False]
        target_pid = int(pid)

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def enum_callback(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                win_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
                if win_pid.value == target_pid:
                    if user32.GetWindowTextLengthW(hwnd) > 0:
                        found[0] = True
                        return False  # stop enumeration
            return True

        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return found[0]
    except Exception:
        return False


def _pid_has_visible_window_linux(pid: int) -> bool:
    """Linux: wmctrl -lp lista ventanas con PID dueño."""
    if not shutil.which("wmctrl"):
        return False
    try:
        result = subprocess.run(
            ["wmctrl", "-lp"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return False
        pid_str = str(pid)
        for line in result.stdout.splitlines():
            parts = line.split(None, 4)
            # wmctrl -lp: hwnd desktop pid host title
            if len(parts) >= 3 and parts[2] == pid_str:
                return True
        return False
    except Exception:
        return False


def _focus_window_linux(process_name: str, window_class: str = None, window_title: str = None) -> bool:
    """Linux: usa xdotool o wmctrl para activar ventana."""
    try:
        # Estrategia 1: xdotool search --class
        if window_class and shutil.which("xdotool"):
            result = subprocess.run(
                ["xdotool", "search", "--class", window_class, "windowactivate"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            )
            if result.returncode == 0:
                return True

        # Estrategia 2: xdotool search --name
        if window_title and shutil.which("xdotool"):
            result = subprocess.run(
                ["xdotool", "search", "--name", window_title, "windowactivate"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            )
            if result.returncode == 0:
                return True

        # Estrategia 3: wmctrl
        if window_class and shutil.which("wmctrl"):
            result = subprocess.run(
                ["wmctrl", "-xa", window_class],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            )
            if result.returncode == 0:
                return True

        return False
    except Exception:
        return False
