"""Tests for the Pomodoro in-session control regex (pause/resume/extend/skip).

Covers regex defined in skills/builtin/schedule/skill.py:
- _POMODORO_PAUSE_RE
- _POMODORO_RESUME_RE
- _POMODORO_EXTEND_RE      (with captured minute group)
- _POMODORO_EXTEND_CYCLE_RE
- _POMODORO_EXTEND_CONFIRM_CURRENT_RE
- _POMODORO_EXTEND_CONFIRM_NEW_RE
- _POMODORO_SKIP_BREAK_RE

Run from backend/:
    pytest tests/test_pomodoro_commands.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.builtin.schedule.skill import (
    _POMODORO_PAUSE_RE,
    _POMODORO_RESUME_RE,
    _POMODORO_EXTEND_RE,
    _POMODORO_EXTEND_CYCLE_RE,
    _POMODORO_EXTEND_CONFIRM_CURRENT_RE,
    _POMODORO_EXTEND_CONFIRM_NEW_RE,
    _POMODORO_SKIP_BREAK_RE,
)


class TestPomodoroPauseRegex:
    def test_pausa_el(self):
        assert _POMODORO_PAUSE_RE.search("pausa el pomodoro")

    def test_pausa_al(self):
        assert _POMODORO_PAUSE_RE.search("pausa al pomodoro")

    def test_pausa_solo(self):
        assert _POMODORO_PAUSE_RE.search("pausa")

    def test_pausa_esto(self):
        assert _POMODORO_PAUSE_RE.search("pausa esto")

    def test_espera_un_momento(self):
        assert _POMODORO_PAUSE_RE.search("espera un momento")

    def test_espera_un_segundo(self):
        assert _POMODORO_PAUSE_RE.search("espera un segundo")

    def test_pausame(self):
        assert _POMODORO_PAUSE_RE.search("pausame el pomodoro")

    def test_no_match_iniciar(self):
        assert not _POMODORO_PAUSE_RE.search("inicia el pomodoro")

    def test_no_match_para(self):
        # "para el pomodoro" is _POMODORO_STOP_RE, not PAUSE
        assert not _POMODORO_PAUSE_RE.search("para el pomodoro")


class TestPomodoroResumeRegex:
    def test_reanuda_el(self):
        assert _POMODORO_RESUME_RE.search("reanuda el pomodoro")

    def test_reanuda_solo(self):
        assert _POMODORO_RESUME_RE.search("reanuda")

    def test_continua(self):
        assert _POMODORO_RESUME_RE.search("continua")

    def test_continua_acento(self):
        assert _POMODORO_RESUME_RE.search("continúa")

    def test_sigue(self):
        assert _POMODORO_RESUME_RE.search("sigue")

    def test_sigamos(self):
        assert _POMODORO_RESUME_RE.search("sigamos")

    def test_retoma(self):
        assert _POMODORO_RESUME_RE.search("retoma el pomodoro")

    def test_volvamos_al_pomodoro(self):
        assert _POMODORO_RESUME_RE.search("volvamos al pomodoro")

    def test_no_match_pausa(self):
        assert not _POMODORO_RESUME_RE.search("pausa")


class TestPomodoroExtendRegex:
    def test_anade_10_min(self):
        m = _POMODORO_EXTEND_RE.search("añade 10 minutos")
        assert m and m.group(1) == "10"

    def test_anade_5_m_corto(self):
        m = _POMODORO_EXTEND_RE.search("añade 5 m")
        assert m and m.group(1) == "5"

    def test_dame_5_mas(self):
        m = _POMODORO_EXTEND_RE.search("dame 5 min más")
        assert m and m.group(1) == "5"

    def test_extiende_15(self):
        m = _POMODORO_EXTEND_RE.search("extiende 15 min")
        assert m and m.group(1) == "15"

    def test_agrega_20_al_pomodoro(self):
        m = _POMODORO_EXTEND_RE.search("agrega 20 minutos al pomodoro")
        assert m and m.group(1) == "20"

    def test_agrega_10_al_ciclo(self):
        m = _POMODORO_EXTEND_RE.search("agrega 10 min al ciclo")
        assert m and m.group(1) == "10"

    def test_suma_3_min(self):
        m = _POMODORO_EXTEND_RE.search("suma 3 min más")
        assert m and m.group(1) == "3"

    def test_no_match_sin_numero(self):
        assert not _POMODORO_EXTEND_RE.search("añade más tiempo")

    def test_no_match_ciclo_extra(self):
        # "agrega un ciclo" should match EXTEND_CYCLE_RE, not EXTEND_RE.
        assert not _POMODORO_EXTEND_RE.search("agrega un ciclo más")


class TestPomodoroExtendCycleRegex:
    def test_anade_ciclo(self):
        assert _POMODORO_EXTEND_CYCLE_RE.search("añade un ciclo más")

    def test_anade_otro_ciclo(self):
        assert _POMODORO_EXTEND_CYCLE_RE.search("añade otro ciclo")

    def test_agrega_ciclo_extra(self):
        assert _POMODORO_EXTEND_CYCLE_RE.search("agrega un ciclo extra")

    def test_suma_ciclo_al_final(self):
        assert _POMODORO_EXTEND_CYCLE_RE.search("suma un ciclo al final")

    def test_no_match_minutes(self):
        assert not _POMODORO_EXTEND_CYCLE_RE.search("añade 10 min")


class TestPomodoroExtendConfirmRegex:
    def test_confirm_current_extiende(self):
        assert _POMODORO_EXTEND_CONFIRM_CURRENT_RE.search("extiende")

    def test_confirm_current_este_ciclo(self):
        assert _POMODORO_EXTEND_CONFIRM_CURRENT_RE.search("este ciclo")

    def test_confirm_current_actual(self):
        assert _POMODORO_EXTEND_CONFIRM_CURRENT_RE.search("actual")

    def test_confirm_new_ciclo_nuevo(self):
        assert _POMODORO_EXTEND_CONFIRM_NEW_RE.search("ciclo nuevo")

    def test_confirm_new_nuevo_ciclo(self):
        assert _POMODORO_EXTEND_CONFIRM_NEW_RE.search("nuevo ciclo")

    def test_confirm_new_otro_ciclo(self):
        assert _POMODORO_EXTEND_CONFIRM_NEW_RE.search("otro ciclo")

    def test_confirm_new_al_final(self):
        assert _POMODORO_EXTEND_CONFIRM_NEW_RE.search("al final")

    def test_no_cross_match(self):
        # confirm_current shouldn't match the new-cycle confirmations.
        assert not _POMODORO_EXTEND_CONFIRM_CURRENT_RE.search("ciclo nuevo")
        assert not _POMODORO_EXTEND_CONFIRM_NEW_RE.search("extiende")


class TestPomodoroSkipBreakRegex:
    def test_salta_el_descanso(self):
        assert _POMODORO_SKIP_BREAK_RE.search("salta el descanso")

    def test_salta_descanso(self):
        assert _POMODORO_SKIP_BREAK_RE.search("salta descanso")

    def test_skip_descanso(self):
        assert _POMODORO_SKIP_BREAK_RE.search("skip el descanso")

    def test_omite_descanso(self):
        assert _POMODORO_SKIP_BREAK_RE.search("omite el descanso")

    def test_siguiente_ciclo_ya(self):
        assert _POMODORO_SKIP_BREAK_RE.search("siguiente ciclo ya")

    def test_proximo_ciclo_ya(self):
        assert _POMODORO_SKIP_BREAK_RE.search("próximo ciclo ya")

    def test_no_match_descansar(self):
        assert not _POMODORO_SKIP_BREAK_RE.search("voy a descansar un rato")
