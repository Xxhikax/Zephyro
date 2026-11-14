"""Detección de juegos por PROCESO, no por título de ventana.

Lo que había antes eran dos substrings ("steam", "epic games") en el título de la ventana
activa, dentro de `_DISTRACTION_APPS`. Un juego en pantalla completa cuyo título no dijera
eso era INVISIBLE para el Pomodoro.

La regla nueva es la misma que `_focus_window_windows` aprendió a los golpes en
`core/platform.py`: el título de una ventana no dice de quién es. Un juego se resuelve por
el ejecutable dueño de la ventana en foco.

Dos semánticas distintas, y la distinción es el punto:
  * LAUNCHER (steam.exe) en la bandeja → NO es jugar. Solo cuenta si está en foco.
  * EJECUTABLE DE JUEGO (valorant.exe) corriendo → es señal, pero para abrir un episodio
    con tiempo tiene que estar al frente. En background va al reporte sin minutos.

Tests puros: sin AppController, sin BrowserConfig, sin red (ver .claude/rules/tests-safety.md).
"""
import time

import pytest

from modules.schedule.monitor import (
    FocusMonitor,
    _GAME_LAUNCHERS,
    _GAME_PROCESSES,
)


def make_monitor():
    return FocusMonitor()


# ── Clasificación por foreground ──────────────────────────────────────────────

class TestForegroundGame:
    def test_sin_escaneo_no_hay_juego(self):
        """Estado por defecto: se comporta igual que antes de existir la feature."""
        m = make_monitor()
        assert m._foreground_game() == ""
        assert m._classify_distraction("Visual Studio Code", "") == (False, "")

    def test_juego_en_foco_es_distraccion_categoria_game(self):
        m = make_monitor()
        m._foreground_proc = "valorant.exe"
        assert m._foreground_game() == "valorant.exe"
        assert m._classify_distraction("VALORANT", "") == (True, "game")

    def test_juego_en_foco_gana_sobre_la_whitelist(self):
        """Un juego cuyo título finja ser un editor no se puede colar.

        Este es el agujero concreto del enfoque por título: "code" está en
        `_BASE_WHITELIST`, así que un título como "Code" habría salido limpio.
        El dueño del proceso no se puede falsear desde el título.
        """
        m = make_monitor()
        m._foreground_proc = "cs2.exe"
        assert m._classify_distraction("Visual Studio Code", "") == (True, "game")

    def test_launcher_en_foco_cuenta(self):
        m = make_monitor()
        m._foreground_proc = "steam.exe"
        assert m._classify_distraction("Steam", "") == (True, "game")

    def test_launcher_en_background_NO_cuenta(self):
        """Steam abierto en la bandeja mientras programás no es una distracción."""
        m = make_monitor()
        m._running_games = {"steam.exe"}
        m._foreground_proc = "code.exe"
        assert m._foreground_game() == ""
        assert m._classify_distraction("proyecto — Visual Studio Code", "") == (False, "")

    def test_proceso_no_juego_en_foco_no_matchea(self):
        m = make_monitor()
        m._foreground_proc = "msedge.exe"
        assert m._foreground_game() == ""


# ── Juegos en background: evidencia, no minutos ───────────────────────────────

class TestBackgroundGames:
    def test_background_va_al_reporte_sin_minutos(self):
        m = make_monitor()
        m._background_games_seen = {"valorant.exe"}
        line = m._background_games_line()
        assert line is not None
        assert "valorant" in line
        assert "min" not in line  # evidencia, no tiempo de distracción

    def test_sin_juegos_no_hay_linea(self):
        assert make_monitor()._background_games_line() is None

    def test_reporte_limpio_igual_menciona_el_juego(self):
        m = make_monitor()
        m._background_games_seen = {"cs2.exe"}
        report = m.get_distraction_report()
        assert "Foco perfecto" in report
        assert "cs2" in report

    def test_background_no_castiga_el_score(self):
        """Tener un juego abierto no resta: no se jugó, no hubo tiempo perdido."""
        m = make_monitor()
        now = time.time()
        m._work_intervals = [[now - 1800, now]]
        m._cycles_completed = 1
        m._background_games_seen = {"valorant.exe"}
        assert m.focus_score() == 100


# ── Escaneo de procesos ───────────────────────────────────────────────────────

class TestGameScan:
    def test_scan_es_una_sola_pasada_de_procesos(self, monkeypatch):
        """Una `process_iter`, no una por nombre.

        `core.platform.is_process_running` barre TODOS los procesos por cada nombre
        consultado; con ~50 nombres serían 50 barridos completos cada 15 segundos.
        """
        import psutil

        calls = {"n": 0}

        class FakeProc:
            def __init__(self, name):
                self.info = {"name": name}

        def fake_iter(attrs=None):
            calls["n"] += 1
            return iter([
                FakeProc("chrome.exe"),
                FakeProc("Valorant.exe"),      # case-insensitive
                FakeProc("steam.exe"),
                FakeProc("python.exe"),
            ])

        monkeypatch.setattr(psutil, "process_iter", fake_iter)
        found, _fg = FocusMonitor._scan_games_sync()
        assert calls["n"] == 1
        assert found == {"valorant.exe", "steam.exe"}

    def test_scan_nunca_explota(self, monkeypatch):
        """El loop de detección no puede morir por un fallo del escaneo."""
        import psutil

        def boom(attrs=None):
            raise RuntimeError("psutil caído")

        monkeypatch.setattr(psutil, "process_iter", boom)
        found, fg = FocusMonitor._scan_games_sync()
        assert found == set()
        assert isinstance(fg, str)

    @pytest.mark.asyncio
    async def test_refresh_marca_background_pero_no_foreground(self, monkeypatch):
        m = make_monitor()

        def fake_scan():
            return {"valorant.exe", "steam.exe"}, "steam.exe"

        monkeypatch.setattr(m, "_scan_games_sync", staticmethod(fake_scan))
        await m._refresh_game_state()
        # valorant corre pero NO está al frente → evidencia de background.
        assert m._background_games_seen == {"valorant.exe"}
        # steam está al frente → es el juego en foco, no "background".
        assert m._foreground_proc == "steam.exe"
        assert m._foreground_game() == "steam.exe"


# ── Invariantes de las listas ─────────────────────────────────────────────────

class TestGameLists:
    def test_javaw_no_esta(self):
        """`javaw.exe` es cualquier app Java, no Minecraft. Falso positivo seguro."""
        assert "javaw.exe" not in _GAME_PROCESSES
        assert "javaw.exe" not in _GAME_LAUNCHERS

    def test_todo_en_minuscula_con_exe(self):
        """El escaneo compara contra `proc.name().lower()`: cualquier entrada con
        mayúsculas sería inalcanzable."""
        for name in _GAME_PROCESSES | _GAME_LAUNCHERS:
            assert name == name.lower(), name
            assert name.endswith(".exe"), name

    def test_los_sets_no_se_solapan(self):
        """Un nombre en ambos sets tendría dos semánticas contradictorias."""
        assert not (_GAME_PROCESSES & _GAME_LAUNCHERS)
