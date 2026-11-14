"""El selector de ventanas elegía ventanas que no eran de la app.

Incidente del 2026-10-02: "abre discord" contestó "no pude traer la ventana al
frente" y acto seguido el escritorio dejó de responder a los clicks.

`_focus_window_windows` tenía tres estrategias en orden, y **las dos primeras
no miraban de quién era la ventana**:

  1. la primera ventana cuyo TÍTULO contuviera el patrón — sin chequear dueño
     ni visibilidad. "discord" matchea una pestaña del navegador titulada
     "… Discord …".
  2. `FindWindowW(clase)`. Medido en la máquina de Bruno:

         FindWindowW('Chrome_WidgetWin_1', None)
           -> msedgewebview2.exe, padre SearchHost.exe
              = el overlay de BÚSQUEDA de Windows 11, invisible, 832x864

     Esa clase la comparten Edge, Discord, CurseForge, los Widgets y el menú
     Inicio. Y después se le hacía `ShowWindow(SW_SHOW)` + `SetForegroundWindow`.

**Aviso honesto:** esto NO está probado como la causa del freeze. El mensaje
que leyó Bruno sale de una línea que corre DESPUÉS de que `focus_window`
devolvió False, así que el backend terminó normalmente. Lo que sí está medido
es que el selector puede elegir —y mostrar a la fuerza— una ventana ajena.
Eso es un defecto propio, y es lo que fijan estos tests.

Los datos de abajo son la lista REAL de ventanas de Discord, medida.
"""
import pytest

from core.platform import WindowCandidate, _pick_window


def _w(**kw):
    base = dict(hwnd=1, title="", cls="", area=0, visible=False, iconic=False,
                toolwindow=False, noactivate=False)
    base.update(kw)
    return WindowCandidate(**base)


# La lista real medida el 2026-10-02 con Discord abierto.
DISCORD_WINDOWS = [
    _w(hwnd=1, title="Discord Overlay", cls="Chrome_WidgetWin_1",
       area=2073600, visible=False, toolwindow=True),
    _w(hwnd=2, title="Amigos - Discord", cls="Chrome_WidgetWin_1",
       area=2028928, visible=True),
    _w(hwnd=3, cls="Chrome_WidgetWin_0", area=0, toolwindow=True),
    _w(hwnd=4, cls="Electron_NotifyIconHostWindow", area=0),
    _w(hwnd=5, title="DDE Server Window", cls="OleDdeWndClass", area=0),
    _w(hwnd=6, cls="crashpad_SessionEndWatcher", area=5304),
    _w(hwnd=7, cls="Chrome_WidgetWin_0", area=1084320),
    _w(hwnd=8, cls="Base_PowerMessageWindow", area=0, noactivate=True),
    _w(hwnd=9, title="Default IME", cls="IME", area=0),
]


def test_the_main_window_wins_over_the_fullscreen_overlay():
    """El overlay de Discord es MÁS GRANDE y es una toolwindow invisible."""
    best, activable = _pick_window(DISCORD_WINDOWS, "Chrome_WidgetWin_1", "Discord")
    assert best.hwnd == 2, f"eligio {best.title!r}"
    assert activable


def test_the_main_window_still_wins_when_discord_is_in_the_tray():
    """Con todo oculto, la principal sigue siendo identificable…"""
    hidden = [
        _w(hwnd=1, title="Discord Overlay", cls="Chrome_WidgetWin_1",
           area=2073600, visible=False, toolwindow=True),
        _w(hwnd=2, title="Amigos - Discord", cls="Chrome_WidgetWin_1",
           area=160 * 28, visible=False),
    ]
    best, activable = _pick_window(hidden, "Chrome_WidgetWin_1", "Discord")
    assert best.hwnd == 2
    # …pero NO se la muestra a la fuerza: está oculta porque Discord lo decidió.
    assert not activable, "iba a hacer SW_SHOW sobre una ventana escondida"


def test_a_minimized_window_is_restorable():
    """Minimizada NO es lo mismo que oculta: el usuario la minimizó y
    restaurarla es exactamente lo que está pidiendo."""
    best, activable = _pick_window(
        [_w(hwnd=7, title="Word", area=900_000, visible=False, iconic=True)]
    )
    assert best.hwnd == 7
    assert activable


def test_a_window_that_refuses_focus_is_never_chosen():
    """WS_EX_NOACTIVATE declara que no quiere el foco."""
    best, activable = _pick_window(
        [_w(hwnd=8, title="power", noactivate=True, visible=True, area=99_999)]
    )
    assert best is None
    assert not activable


def test_nothing_at_all():
    assert _pick_window([]) == (None, False)


# ── Clase y título son DESEMPATES, nunca selectores ─────────────────────
def test_a_shared_window_class_cannot_select_by_itself():
    """`Chrome_WidgetWin_1` la comparten Edge, Discord, los Widgets y Búsqueda.

    Acá todas las candidatas ya son del proceso correcto (el cruce por PID
    pasa antes). Lo que se fija es que el bonus por clase —5 puntos— no pueda
    dar vuelta la decisión contra una ventana visible y grande.
    """
    best, _ = _pick_window(
        [
            _w(hwnd=1, title="", cls="Chrome_WidgetWin_1", area=0),      # clase OK, inútil
            _w(hwnd=2, title="Lo real", cls="otra", area=900_000, visible=True),
        ],
        window_class="Chrome_WidgetWin_1",
    )
    assert best.hwnd == 2, "el bonus de clase le gano a una ventana real"


def test_a_title_match_cannot_beat_a_real_window_either():
    best, _ = _pick_window(
        [
            _w(hwnd=1, title="Discord", area=0),                          # titulo OK, 0x0
            _w(hwnd=2, title="otra cosa", area=900_000, visible=True),
        ],
        window_title="Discord",
    )
    assert best.hwnd == 2


def test_the_title_breaks_a_tie_between_equals():
    """Contraparte legítima: cuando todo lo demás empata, el título decide."""
    best, _ = _pick_window(
        [
            _w(hwnd=1, title="Documento sin titulo", area=900_000, visible=True),
            _w(hwnd=2, title="Informe - Word", area=900_000, visible=True),
        ],
        window_title="Word",
    )
    assert best.hwnd == 2


# ── Una app normal se sigue enfocando: el filtro no puede ser tan duro ──
@pytest.mark.parametrize("app", [
    _w(hwnd=1, title="Bloc de notas", cls="Notepad", area=600_000, visible=True),
    _w(hwnd=2, title="Calculadora", cls="ApplicationFrameWindow", area=300_000, visible=True),
    _w(hwnd=3, title="Documento1 - Word", cls="OpusApp", area=1_500_000, visible=True),
])
def test_an_ordinary_visible_window_is_still_chosen(app):
    best, activable = _pick_window([app])
    assert best is app
    assert activable
