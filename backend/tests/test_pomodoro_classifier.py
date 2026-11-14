"""Tests for the Pomodoro classifier: regex patterns + DeepSeek LLM extractors.

Cubre tres capas:
  1. Regex fast-path: _POMODORO_START_RE, _POMODORO_STOP_RE, _POMODORO_HINT_RE,
     _POMO_CFG_CYCLES_RE, _POMO_CFG_WORK_RE, _POMO_CFG_BREAK_RE
  2. JSON parser: _parse_schedule_llm_json
  3. DeepSeek v4 flash extractors (mocked): _extract_pomodoro_intent_llm,
     _parse_pomodoro_config_llm
  4. FocusMonitor unified distraction state (vision + app fusion, should_speak)
  5. FocusMonitor pause/extend/skip control surface
  6. ScheduleSkill extend heuristic (current vs new-cycle confirmation)

Ejecutar desde backend/:
    pytest tests/test_pomodoro_classifier.py -v
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.builtin.schedule.skill import (
    _POMODORO_HINT_RE,
    _POMODORO_MODE_RE,
    _POMODORO_START_RE,
    _POMODORO_STOP_RE,
    _POMO_CFG_BREAK_RE,
    _POMO_CFG_CYCLES_RE,
    _POMO_CFG_WORK_RE,
    _WORD_CYCLE_MAP,
    _parse_schedule_llm_json,
    ScheduleSkill,
)
from skills.base import SkillMetadata


@pytest.fixture
def skill():
    metadata = SkillMetadata(name="schedule", description="test schedule skill")
    return ScheduleSkill(metadata=metadata, skill_dir=Path("."))


# ═══════════════════════════════════════════════════════════════════
# 1. Regex: _POMODORO_START_RE
# ═══════════════════════════════════════════════════════════════════

class TestPomodoroStartRegex:
    """Verbos de activación + estructuras con duración y tarea."""

    # ── Verbos imperativo/presente ─────────────────────────────────
    def test_activa(self):
        assert _POMODORO_START_RE.search("activa pomodoro")

    def test_activa_el(self):
        assert _POMODORO_START_RE.search("activa el pomodoro")

    def test_inicia(self):
        assert _POMODORO_START_RE.search("inicia pomodoro")

    def test_inicia_sesion(self):
        assert _POMODORO_START_RE.search("inicia una sesión de pomodoro")

    def test_empieza(self):
        assert _POMODORO_START_RE.search("empieza pomodoro")

    def test_empieza_el(self):
        assert _POMODORO_START_RE.search("empieza el pomodoro")

    def test_arranca(self):
        assert _POMODORO_START_RE.search("arranca pomodoro")

    def test_comienza_infinitivo(self):
        # "comenzar" sí matchea (comenz+a+r); "comienza" (stem-change) es gap conocido del regex
        assert _POMODORO_START_RE.search("comenzar pomodoro")

    def test_comienza_presente(self):
        assert _POMODORO_START_RE.search("comienza pomodoro")

    def test_start(self):
        assert _POMODORO_START_RE.search("start pomodoro")

    def test_quiero(self):
        assert _POMODORO_START_RE.search("quiero pomodoro")

    def test_pon(self):
        assert _POMODORO_START_RE.search("pon pomodoro")

    def test_lanza(self):
        assert _POMODORO_START_RE.search("lanza pomodoro")

    # ── Infinitivos ────────────────────────────────────────────────
    def test_activar(self):
        assert _POMODORO_START_RE.search("activar pomodoro")

    def test_iniciar(self):
        assert _POMODORO_START_RE.search("iniciar el pomodoro")

    def test_comenzar(self):
        assert _POMODORO_START_RE.search("comenzar pomodoro")

    def test_arrancar(self):
        assert _POMODORO_START_RE.search("arrancar pomodoro")

    # ── Con duración ───────────────────────────────────────────────
    def test_con_duracion_minutos(self):
        m = _POMODORO_START_RE.search("activa pomodoro de 45 min")
        assert m
        assert m.group(1) == "45"

    def test_con_duracion_minutos_completo(self):
        m = _POMODORO_START_RE.search("activa pomodoro de 30 minutos")
        assert m
        assert m.group(1) == "30"

    def test_inicia_con_duracion(self):
        m = _POMODORO_START_RE.search("inicia pomodoro de 25 min")
        assert m
        assert m.group(1) == "25"

    # ── Con tarea ──────────────────────────────────────────────────
    def test_con_tarea_para(self):
        m = _POMODORO_START_RE.search("activa pomodoro para cálculo")
        assert m
        assert "cálculo" in (m.group("task") or "")

    def test_con_duracion_y_tarea(self):
        m = _POMODORO_START_RE.search("activa pomodoro de 45 min para análisis matemático")
        assert m
        assert m.group(1) == "45"

    def test_con_tarea_de(self):
        m = _POMODORO_START_RE.search("activa pomodoro de álgebra")
        assert m

    # ── Acentos y variantes ────────────────────────────────────────
    def test_activa_acento_gap(self):
        # Gap: "actívame" tiene acento en 'í' — el charset activ[aoeéó] no lo cubre
        assert not _POMODORO_START_RE.search("actívame el pomodoro")  # gap documentado

    def test_inicia_acento(self):
        assert _POMODORO_START_RE.search("inició el pomodoro")  # pasado

    def test_empezo(self):
        assert _POMODORO_START_RE.search("empezó el pomodoro")

    # ── Negativos: no deben matchear START_RE ──────────────────────
    def test_no_match_solo_hint(self):
        assert not _POMODORO_START_RE.search("una sesión de pomodoro")

    def test_no_match_pomodoro_solo(self):
        assert not _POMODORO_START_RE.search("el pomodoro de hoy")

    def test_no_match_stop(self):
        assert not _POMODORO_START_RE.search("para el pomodoro")

    def test_no_match_status(self):
        assert not _POMODORO_START_RE.search("cómo va el pomodoro")


# ═══════════════════════════════════════════════════════════════════
# 2. Regex: _POMODORO_STOP_RE
# ═══════════════════════════════════════════════════════════════════

class TestPomodoroStopRegex:
    def test_para_el(self):
        assert _POMODORO_STOP_RE.search("para el pomodoro")

    def test_para_sin_el(self):
        assert _POMODORO_STOP_RE.search("para el pomodoro")

    def test_deten_con_acento(self):
        assert _POMODORO_STOP_RE.search("detén el pomodoro")

    def test_deten_sin_acento(self):
        assert _POMODORO_STOP_RE.search("deten el pomodoro")

    def test_cancela(self):
        assert _POMODORO_STOP_RE.search("cancela el pomodoro")

    def test_cancela_sin_el(self):
        assert _POMODORO_STOP_RE.search("cancela pomodoro")

    def test_stop(self):
        assert _POMODORO_STOP_RE.search("stop pomodoro")

    def test_no_match_activa(self):
        assert not _POMODORO_STOP_RE.search("activa pomodoro")

    def test_no_match_status(self):
        assert not _POMODORO_STOP_RE.search("cómo va el pomodoro")


# ═══════════════════════════════════════════════════════════════════
# 3. Regex: _POMODORO_HINT_RE (trigger para LLM fallback)
# ═══════════════════════════════════════════════════════════════════

class TestPomodoroHintRegex:
    """Menciones sueltas de 'pomodoro' sin verbo claro → disparan DeepSeek."""

    def test_solo_pomodoro(self):
        assert _POMODORO_HINT_RE.search("pomodoro")

    def test_el_pomodoro(self):
        assert _POMODORO_HINT_RE.search("el pomodoro")

    def test_una_sesion_de_pomodoro(self):
        assert _POMODORO_HINT_RE.search("una sesión de pomodoro")

    def test_sesion_pomodoro(self):
        assert _POMODORO_HINT_RE.search("sesión de pomodoro para hoy")

    def test_pomodoros_plural(self):
        assert _POMODORO_HINT_RE.search("hacer pomodoros hoy")

    def test_modo_pomodoro(self):
        assert _POMODORO_HINT_RE.search("modo pomodoro")

    def test_pomodoro_con_duracion_sin_verbo(self):
        assert _POMODORO_HINT_RE.search("pomodoro de 45 min")

    def test_no_match_texto_sin_pomodoro(self):
        assert not _POMODORO_HINT_RE.search("agenda una reunión mañana")

    # Confirmar que START_RE también matchea cuando hay verbo
    def test_start_re_takes_precedence(self):
        txt = "activa pomodoro de 45 min"
        assert _POMODORO_START_RE.search(txt)   # verbo presente
        assert _POMODORO_HINT_RE.search(txt)    # hint también matchea (el handler evalúa START primero)


# ═══════════════════════════════════════════════════════════════════
# 4. Regex: _POMODORO_MODE_RE
# ═══════════════════════════════════════════════════════════════════

class TestPomodoroModeRegex:
    def test_modo_pomodoro(self):
        assert _POMODORO_MODE_RE.search("modo pomodoro")

    def test_pomodoro_mode(self):
        assert _POMODORO_MODE_RE.search("pomodoro mode")

    def test_no_match_activa_pomodoro(self):
        # "activa pomodoro" no contiene "modo pomodoro"
        assert not _POMODORO_MODE_RE.search("activa pomodoro")


# ═══════════════════════════════════════════════════════════════════
# 5. Regex: config extractors
# ═══════════════════════════════════════════════════════════════════

class TestPomodoroCfgRegex:
    """Config regex para cuando el usuario responde al setup interactivo."""

    # ── Cycles ────────────────────────────────────────────────────
    def test_ciclos_numerico(self):
        m = _POMO_CFG_CYCLES_RE.search("4 ciclos")
        assert m and m.group(1) == "4"

    def test_ciclos_numerico_con_ruido(self):
        m = _POMO_CFG_CYCLES_RE.search("quiero 3 ciclos de trabajo")
        assert m and m.group(1) == "3"

    def test_ciclos_palabra_cuatro(self):
        m = _POMO_CFG_CYCLES_RE.search("cuatro ciclos")
        assert m
        word = "cuatro"
        assert _WORD_CYCLE_MAP[word] == 4

    def test_ciclos_palabra_dos(self):
        m = _POMO_CFG_CYCLES_RE.search("dos ciclos")
        assert m
        assert _WORD_CYCLE_MAP["dos"] == 2

    def test_ciclos_palabra_tres(self):
        m = _POMO_CFG_CYCLES_RE.search("tres ciclos")
        assert m
        assert _WORD_CYCLE_MAP["tres"] == 3

    def test_no_match_ciclos(self):
        assert not _POMO_CFG_CYCLES_RE.search("trabajo de 45 min")

    # ── Work time ─────────────────────────────────────────────────
    def test_work_orden_normal(self):
        m = _POMO_CFG_WORK_RE.search("45 min de trabajo")
        assert m and (m.group(1) or m.group(2)) == "45"

    def test_work_orden_invertido(self):
        m = _POMO_CFG_WORK_RE.search("trabajo serían 45 minutos")
        assert m and (m.group(1) or m.group(2)) == "45"

    def test_work_estudio(self):
        m = _POMO_CFG_WORK_RE.search("45 minutos de estudio")
        assert m and (m.group(1) or m.group(2)) == "45"

    def test_work_estudio_invertido(self):
        m = _POMO_CFG_WORK_RE.search("estudio de 30 min")
        assert m and (m.group(1) or m.group(2)) == "30"

    def test_work_no_match_descanso(self):
        assert not _POMO_CFG_WORK_RE.search("10 min de descanso")

    # ── Break time ────────────────────────────────────────────────
    def test_break_orden_normal(self):
        m = _POMO_CFG_BREAK_RE.search("10 min de descanso")
        assert m and (m.group(1) or m.group(2)) == "10"

    def test_break_orden_invertido(self):
        m = _POMO_CFG_BREAK_RE.search("descanso de 5 min")
        assert m and (m.group(1) or m.group(2)) == "5"

    def test_break_minutos(self):
        m = _POMO_CFG_BREAK_RE.search("5 minutos de descanso")
        assert m and (m.group(1) or m.group(2)) == "5"

    def test_break_no_match_trabajo(self):
        assert not _POMO_CFG_BREAK_RE.search("45 min de trabajo")


# ═══════════════════════════════════════════════════════════════════
# 6. _parse_schedule_llm_json
# ═══════════════════════════════════════════════════════════════════

class TestParseLlmJson:
    def test_json_limpio_intent(self):
        raw = '{"action": "start", "work_min": 45, "cycles": 4, "break_min": 5, "task": "cálculo"}'
        result = _parse_schedule_llm_json(raw)
        assert result == {"action": "start", "work_min": 45, "cycles": 4, "break_min": 5, "task": "cálculo"}

    def test_json_limpio_config(self):
        raw = '{"task": "análisis", "cycles": 2, "work_min": 30, "break_min": 10}'
        result = _parse_schedule_llm_json(raw)
        assert result["task"] == "análisis"
        assert result["cycles"] == 2

    def test_json_en_markdown(self):
        raw = '```json\n{"action": "stop"}\n```'
        result = _parse_schedule_llm_json(raw)
        assert result == {"action": "stop"}

    def test_json_en_markdown_sin_lang(self):
        raw = '```\n{"action": "status"}\n```'
        result = _parse_schedule_llm_json(raw)
        assert result == {"action": "status"}

    def test_json_con_texto_alrededor(self):
        raw = 'La respuesta es: {"action": "start", "work_min": 25}'
        result = _parse_schedule_llm_json(raw)
        assert result["action"] == "start"

    def test_null_fields(self):
        raw = '{"action": "start", "work_min": null, "cycles": 4, "break_min": null, "task": null}'
        result = _parse_schedule_llm_json(raw)
        assert result is not None
        assert result["work_min"] is None
        assert result["cycles"] == 4

    def test_json_invalido_retorna_none(self):
        assert _parse_schedule_llm_json("esto no es json") is None

    def test_json_malformado_retorna_none(self):
        assert _parse_schedule_llm_json('{"action": "start"') is None

    def test_json_vacio(self):
        result = _parse_schedule_llm_json("{}")
        assert result == {}

    def test_action_none(self):
        result = _parse_schedule_llm_json('{"action": "none"}')
        assert result["action"] == "none"


# ═══════════════════════════════════════════════════════════════════
# 7. _extract_pomodoro_intent_llm — DeepSeek v4 flash (mocked)
# ═══════════════════════════════════════════════════════════════════

DEEPSEEK_TARGET = "core.llm_helpers.call_deepseek_flash_json"


class TestExtractPomodoroIntentLlm:
    """Prueba la extracción de intención cuando HINT_RE matchea pero START_RE no."""

    async def test_start_con_config_completa(self, skill):
        raw = '{"action": "start", "work_min": 45, "cycles": 4, "break_min": 5, "task": "cálculo"}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._extract_pomodoro_intent_llm("quiero una sesión de pomodoro para cálculo")
        assert result["action"] == "start"
        assert result["work_min"] == 45
        assert result["cycles"] == 4
        assert result["task"] == "cálculo"

    async def test_start_sin_config(self, skill):
        raw = '{"action": "start", "work_min": null, "cycles": null, "break_min": null, "task": null}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._extract_pomodoro_intent_llm("pomodoro")
        assert result["action"] == "start"

    async def test_stop(self, skill):
        raw = '{"action": "stop", "work_min": null, "cycles": null, "break_min": null, "task": null}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._extract_pomodoro_intent_llm("pomodoro ya listo termina")
        assert result["action"] == "stop"

    async def test_status(self, skill):
        raw = '{"action": "status", "work_min": null, "cycles": null, "break_min": null, "task": null}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._extract_pomodoro_intent_llm("pomodoro cuánto falta")
        assert result["action"] == "status"

    async def test_config_parcial(self, skill):
        raw = '{"action": "config", "work_min": 50, "cycles": null, "break_min": null, "task": "proyecto"}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._extract_pomodoro_intent_llm("pomodoro de 50 min para proyecto")
        assert result["action"] == "config"
        assert result["work_min"] == 50
        assert result["task"] == "proyecto"

    async def test_action_none_retorna_start_fallback(self, skill):
        raw = '{"action": "none", "work_min": null, "cycles": null, "break_min": null, "task": null}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._extract_pomodoro_intent_llm("algo raro sobre pomodoro")
        # action=none → el caller trata como activate, pero el extractor retorna "none"
        assert result["action"] == "none"

    async def test_json_invalido_retorna_fallback(self, skill):
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value="no es json")):
            result = await skill._extract_pomodoro_intent_llm("pomodoro raro")
        assert result == {"action": "start"}  # fallback seguro

    async def test_deepseek_lanza_excepcion(self, skill):
        with patch(DEEPSEEK_TARGET, new=AsyncMock(side_effect=Exception("timeout"))):
            result = await skill._extract_pomodoro_intent_llm("pomodoro")
        assert result == {"action": "start"}  # fallback seguro

    async def test_deepseek_retorna_none(self, skill):
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=None)):
            result = await skill._extract_pomodoro_intent_llm("pomodoro")
        assert result == {"action": "start"}

    async def test_markdown_json(self, skill):
        raw = '```json\n{"action": "start", "work_min": 25, "cycles": 4, "break_min": 5, "task": null}\n```'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._extract_pomodoro_intent_llm("una sesión de pomodoro clásica")
        assert result["action"] == "start"
        assert result["work_min"] == 25


# ═══════════════════════════════════════════════════════════════════
# 8. _parse_pomodoro_config_llm — DeepSeek v4 flash (mocked)
# ═══════════════════════════════════════════════════════════════════

class TestParsePomodoroConfigLlm:
    """Prueba la extracción de config cuando el usuario responde al setup interactivo."""

    async def test_config_completa(self, skill):
        raw = '{"task": "cálculo", "cycles": 4, "work_min": 45, "break_min": 10}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._parse_pomodoro_config_llm("cálculo, 4 ciclos, 45 min trabajo, 10 descanso")
        assert result["task"] == "cálculo"
        assert result["cycles"] == 4
        assert result["work_min"] == 45
        assert result["break_min"] == 10

    async def test_config_orden_invertido(self, skill):
        raw = '{"task": "álgebra", "cycles": 3, "work_min": 45, "break_min": 5}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._parse_pomodoro_config_llm("trabajo serían 45 min, tres ciclos, álgebra")
        assert result["work_min"] == 45
        assert result["cycles"] == 3

    async def test_tarea_solamente(self, skill):
        raw = '{"task": "física", "cycles": null, "work_min": null, "break_min": null}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._parse_pomodoro_config_llm("para física")
        assert result["task"] == "física"
        assert result.get("work_min") is None

    async def test_numeros_palabra(self, skill):
        raw = '{"task": null, "cycles": 2, "work_min": 30, "break_min": 5}'
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=raw)):
            result = await skill._parse_pomodoro_config_llm("dos ciclos de treinta minutos")
        assert result["cycles"] == 2
        assert result["work_min"] == 30

    async def test_deepseek_retorna_json_invalido(self, skill):
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value="no entendí")):
            result = await skill._parse_pomodoro_config_llm("algo confuso")
        assert result == {}

    async def test_deepseek_lanza_excepcion(self, skill):
        with patch(DEEPSEEK_TARGET, new=AsyncMock(side_effect=RuntimeError("api down"))):
            result = await skill._parse_pomodoro_config_llm("4 ciclos de 25 min")
        assert result == {}

    async def test_deepseek_retorna_none(self, skill):
        with patch(DEEPSEEK_TARGET, new=AsyncMock(return_value=None)):
            result = await skill._parse_pomodoro_config_llm("2 ciclos")
        assert result == {}


# ═══════════════════════════════════════════════════════════════════
# 9. FocusMonitor — unified distraction state (vision + app fusion)
# ═══════════════════════════════════════════════════════════════════

class _RecordingRegistry:
    """Stub WS registry that captures every broadcast payload."""
    def __init__(self):
        self.payloads = []

    async def broadcast(self, payload):
        self.payloads.append(payload)


def _make_monitor():
    """Build a FocusMonitor with a recording registry and no real loops."""
    from modules.schedule.monitor import FocusMonitor
    m = FocusMonitor()
    m._ws_registry = _RecordingRegistry()
    return m


class TestUnifiedDistractionState:
    """_register_vision_evidence merges vision findings into _distraction_state
    so the alert escalation pipeline (_send_alert levels 1-4) drives all output.
    """

    async def test_vision_only_opens_episode(self):
        m = _make_monitor()
        await m._register_vision_evidence(
            on_task=False, category="game", suggestion="Volvamos al curso", confidence=0.9,
        )
        s = m._distraction_state
        assert s["source"] == "vision"
        assert s["app"] == "game"
        assert s["vision_category"] == "game"
        assert s["alert_level"] == 1
        # One broadcast: the immediate level-1 alert.
        assert len(m._ws_registry.payloads) == 1
        p = m._ws_registry.payloads[0]
        assert p["type"] == "focus_alert"
        assert p["level"] == 1
        assert p["source"] == "vision"
        assert p["should_speak"] is False  # level 1 → no TTS

    async def test_vision_fuses_into_app_episode(self):
        m = _make_monitor()
        # Simulate an app-detected episode already open.
        m._distraction_state.update({
            "app": "youtube.com",
            "url": "https://youtube.com/watch?v=x",
            "started_at": 1000.0,
            "grace_expires_at": 1000.0,
            "alert_level": 1,
            "source": "app",
        })
        m._ws_registry.payloads.clear()
        await m._register_vision_evidence(
            on_task=False, category="video", suggestion="", confidence=1.0,
        )
        s = m._distraction_state
        assert s["source"] == "vision+app"
        assert s["app"] == "youtube.com"  # original app preserved
        assert s["vision_category"] == "video"
        # Fusion path does NOT re-trigger _send_alert — that's the app loop's job.
        assert m._ws_registry.payloads == []

    async def test_on_task_logged_not_broadcast(self):
        m = _make_monitor()
        assert m._on_task_count == 0
        await m._register_vision_evidence(
            on_task=True, category="study", suggestion="", confidence=0.95,
        )
        assert m._on_task_count == 1
        assert m._off_task_count == 0
        # No state change, no broadcast.
        assert m._distraction_state["app"] == ""
        assert m._ws_registry.payloads == []


class TestShouldSpeakFlag:
    """_send_alert emits should_speak=True only for level≥2."""

    async def test_level1_should_not_speak(self):
        m = _make_monitor()
        m._distraction_state["source"] = "app"
        await m._send_alert(1, "youtube", 5)
        assert m._ws_registry.payloads[-1]["should_speak"] is False

    async def test_level2_should_speak(self):
        m = _make_monitor()
        m._distraction_state["source"] = "app"
        await m._send_alert(2, "youtube", 130)
        assert m._ws_registry.payloads[-1]["should_speak"] is True

    async def test_level3_should_speak(self):
        m = _make_monitor()
        m._distraction_state["source"] = "app"
        await m._send_alert(3, "youtube", 250)
        assert m._ws_registry.payloads[-1]["should_speak"] is True


# ═══════════════════════════════════════════════════════════════════
# 10. FocusMonitor — pause/extend/skip control surface
# ═══════════════════════════════════════════════════════════════════

def _fake_running_task():
    """A task-like object that reports as not-done. Used so the control
    methods proceed past their 'no active session' guards."""
    t = MagicMock()
    t.done.return_value = False
    return t


class TestPauseResume:
    async def test_pause_sets_flag_and_clears_event(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "work"
        m._pomodoro_start_ts = 1000.0
        m._pomodoro_duration_min = 25
        result = await m.pause_pomodoro()
        assert "pausado" in result.lower()
        assert m._pomodoro_paused is True
        assert not m._pomodoro_pause_event.is_set()
        # A WS broadcast was emitted for the pause action.
        assert any(p.get("action") == "paused" for p in m._ws_registry.payloads)

    async def test_resume_sets_event_and_broadcasts(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "work"
        m._pomodoro_start_ts = 1000.0
        m._pomodoro_duration_min = 25
        m._pomodoro_pause_event = asyncio.Event()
        m._pomodoro_paused = True
        m._pomodoro_pause_event.clear()
        m._ws_registry.payloads.clear()
        result = await m.resume_pomodoro()
        assert "reanudado" in result.lower()
        assert m._pomodoro_paused is False
        assert m._pomodoro_pause_event.is_set()
        assert any(p.get("action") == "resumed" for p in m._ws_registry.payloads)

    async def test_pause_twice_is_idempotent(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "work"
        m._pomodoro_start_ts = 1000.0
        m._pomodoro_duration_min = 25
        await m.pause_pomodoro()
        result = await m.pause_pomodoro()
        assert "ya está pausado" in result.lower()

    async def test_pause_without_active_session(self):
        m = _make_monitor()
        m._pomodoro_task = None
        result = await m.pause_pomodoro()
        assert "no hay" in result.lower()


class TestExtend:
    async def test_extend_current_queues_minutes(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "work"
        m._pomodoro_start_ts = 1000.0
        m._pomodoro_duration_min = 25
        result = await m.extend_pomodoro(5, mode="current")
        assert m._pomodoro_extend_pending == 5
        assert "extiendo" in result.lower()

    async def test_extend_current_accumulates(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "work"
        m._pomodoro_start_ts = 1000.0
        m._pomodoro_duration_min = 25
        await m.extend_pomodoro(5, mode="current")
        await m.extend_pomodoro(3, mode="current")
        assert m._pomodoro_extend_pending == 8

    async def test_extend_new_cycle_bumps_total(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "work"
        m._pomodoro_start_ts = 1000.0
        m._pomodoro_duration_min = 25
        m._total_cycles = 4
        result = await m.extend_pomodoro(0, mode="new_cycle")
        assert m._total_cycles == 5
        assert "ciclo extra" in result.lower()
        assert any(p.get("action") == "extended" for p in m._ws_registry.payloads)

    async def test_extend_zero_minutes_rejected(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "work"
        m._pomodoro_start_ts = 1000.0
        m._pomodoro_duration_min = 25
        result = await m.extend_pomodoro(0, mode="current")
        assert "indicá" in result.lower() or "indica" in result.lower()
        assert m._pomodoro_extend_pending == 0

    async def test_extend_without_active_session(self):
        m = _make_monitor()
        m._pomodoro_task = None
        result = await m.extend_pomodoro(10, mode="current")
        assert "no hay" in result.lower()


class TestSkipBreak:
    async def test_skip_during_short_break(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "short_break"
        result = await m.skip_break()
        assert m._pomodoro_skip_current is True
        assert "saltando" in result.lower()

    async def test_skip_during_long_break(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "long_break"
        result = await m.skip_break()
        assert m._pomodoro_skip_current is True

    async def test_skip_during_work_rejected(self):
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "work"
        result = await m.skip_break()
        assert "no hay descanso" in result.lower()
        assert m._pomodoro_skip_current is False

    async def test_skip_unpauses(self):
        """Skip during a paused break must release the pause so the sleep
        loop can observe the skip flag immediately."""
        m = _make_monitor()
        m._pomodoro_task = _fake_running_task()
        m._pomodoro_phase = "short_break"
        m._pomodoro_paused = True
        m._pomodoro_pause_event = asyncio.Event()
        m._pomodoro_pause_event.clear()
        await m.skip_break()
        assert m._pomodoro_paused is False
        assert m._pomodoro_pause_event.is_set()


class TestNextPhaseAndDetail:
    """_next_phase_info drives the BG widget 'luego: …' preview."""

    def test_work_to_short_break(self):
        m = _make_monitor()
        m._pomodoro_phase = "work"
        m._total_cycles = 4
        m._pomodoro_duration_min = 25
        m._short_break_min = 5
        m._long_break_min = 15
        next_phase, dur = m._next_phase_info(current_cycle=1)
        assert next_phase == "short_break"
        assert dur == 300

    def test_work_to_long_break_every_4th(self):
        m = _make_monitor()
        m._pomodoro_phase = "work"
        m._total_cycles = 8
        m._pomodoro_duration_min = 25
        m._long_break_min = 15
        m._short_break_min = 5
        next_phase, dur = m._next_phase_info(current_cycle=4)
        assert next_phase == "long_break"
        assert dur == 900

    def test_last_work_has_no_next(self):
        m = _make_monitor()
        m._pomodoro_phase = "work"
        m._total_cycles = 4
        next_phase, dur = m._next_phase_info(current_cycle=4)
        assert next_phase == ""
        assert dur == 0

    def test_break_to_work(self):
        m = _make_monitor()
        m._pomodoro_phase = "short_break"
        m._total_cycles = 4
        m._pomodoro_duration_min = 25
        next_phase, dur = m._next_phase_info(current_cycle=2)
        assert next_phase == "work"
        assert dur == 1500


class TestFocusSummary:
    """El % de foco sale del TIEMPO real, no de los contadores de visión.

    Este bloque reemplaza a `test_no_vision_checks_defaults_100_pct`, que asertaba el
    bug: el summary salía SOLO de `_on_task_count`/`_off_task_count`, que únicamente
    incrementa el tier de visión (apagado salvo `strict_focus`). Con `total_vision == 0`
    devolvía 100 fijo, así que el reporte decía "Foco: 100% on-task" en la misma
    pantalla que "1 episodio(s), 1 min de distracción".
    """

    @staticmethod
    def _with_work(m, work_min, distractions=()):
        """Simula una sesión cerrada: un tramo de trabajo y sus episodios."""
        now = time.time()
        start = now - work_min * 60
        m._work_intervals = [[start, now]]
        m._cycles_completed = 1
        for offset_min, dur_min, category in distractions:
            ep_start = start + offset_min * 60
            ep_end = ep_start + dur_min * 60
            m._distraction_log.append({
                "app": f"{category}-app", "url": "",
                "started_at": ep_start, "ended_at": ep_end,
                "duration_sec": ep_end - ep_start,
                "max_alert_level": 0, "enforced": False,
                "category": category,
            })
        return m

    def test_sin_trabajo_suficiente_no_inventa_porcentaje(self):
        m = _make_monitor()
        out = m._pomodoro_focus_summary()
        assert "100%" not in out
        assert "insuficiente" in out
        assert m.focus_score() is None

    def test_porcentaje_por_tiempo_sin_vision(self):
        m = self._with_work(_make_monitor(), 45, [(5, 9, "browser")])
        out = m._pomodoro_focus_summary()
        # 45 min de trabajo, 9 distraído → 80% on-task. Visión apagada.
        assert "80% on-task" in out
        assert "45 min de trabajo efectivo" in out
        assert "visión" not in out

    def test_sesion_limpia_da_100(self):
        m = self._with_work(_make_monitor(), 25)
        assert "100% on-task" in m._pomodoro_focus_summary()
        assert m.focus_score() == 100

    def test_distraccion_en_pausa_no_cuenta(self):
        """Un episodio fuera de los tramos de trabajo no puede castigar el score."""
        m = _make_monitor()
        now = time.time()
        # Trabajo: [-20min, -10min]. Pausa desde -10min. Distracción en la pausa.
        m._work_intervals = [[now - 1200, now - 600]]
        m._cycles_completed = 1
        m._distraction_log.append({
            "app": "youtube", "url": "", "started_at": now - 500,
            "ended_at": now - 100, "duration_sec": 400,
            "max_alert_level": 0, "enforced": False, "category": "browser",
        })
        assert m._distracted_seconds() == 0
        assert "100% on-task" in m._pomodoro_focus_summary()
        assert m.focus_score() == 100

    def test_episodio_que_cruza_el_borde_se_clipea(self):
        m = _make_monitor()
        now = time.time()
        m._work_intervals = [[now - 600, now - 300]]   # 5 min de trabajo
        m._cycles_completed = 1
        # Episodio de 5 min, del cual solo 2 caen dentro del trabajo.
        m._distraction_log.append({
            "app": "discord", "url": "", "started_at": now - 420,
            "ended_at": now - 120, "duration_sec": 300,
            "max_alert_level": 0, "enforced": False, "category": "messaging",
        })
        assert 115 <= m._distracted_seconds() <= 125

    def test_distraccion_nunca_supera_el_trabajo(self):
        m = self._with_work(_make_monitor(), 10, [(0, 60, "game")])
        out = m._pomodoro_focus_summary()
        assert "0% on-task" in out
        assert m.focus_score() == 0  # tope de castigo por tiempo

    def test_vision_es_una_linea_aparte(self):
        m = self._with_work(_make_monitor(), 30)
        m._on_task_count = 18
        m._off_task_count = 2
        out = m._pomodoro_focus_summary()
        assert "100% on-task" in out      # tiempo: sin distracciones
        assert "visión: 90% on-task" in out  # visión: 18/20, señal independiente

    def test_pause_minutes_rendered(self):
        m = _make_monitor()
        m._pomodoro_total_pauses_sec = 185.0  # 3 minutes
        assert "3 min" in m._pomodoro_focus_summary()


class TestFocusScore:
    def test_peso_por_categoria(self):
        """Mismo tiempo distraído, distinto castigo según la categoría."""
        def score_for(category):
            m = TestFocusSummary._with_work(_make_monitor(), 60, [(0, 5, category)])
            return m.focus_score()
        # game (8) castiga más que browser (5), que más que messaging (2).
        assert score_for("game") < score_for("browser") < score_for("messaging")
        assert score_for("game") == 60       # 100 - 5min*8
        assert score_for("browser") == 75    # 100 - 5min*5
        assert score_for("messaging") == 90  # 100 - 5min*2

    def test_cap_de_tiempo_es_por_sesion_no_por_episodio(self):
        """Partir una distracción en pedazos no puede abaratarla."""
        entera = TestFocusSummary._with_work(
            _make_monitor(), 120, [(0, 30, "game")]).focus_score()
        partida = TestFocusSummary._with_work(
            _make_monitor(), 120,
            [(0, 10, "game"), (20, 10, "game"), (40, 10, "game")]).focus_score()
        assert entera == partida == 50  # ambas tocan el tope de 50

    def test_alertas_se_cuentan_acumulativas(self):
        """max_alert_level es severidad, no un contador: un episodio que llegó a 3
        también emitió la de 2, así que cuenta en ambos umbrales."""
        m = TestFocusSummary._with_work(_make_monitor(), 60)
        m._distraction_log.append({
            "app": "x", "url": "", "started_at": 0.0, "ended_at": 0.0,
            "duration_sec": 0.0, "max_alert_level": 3, "enforced": False,
            "category": "browser",
        })
        counts = m._alert_counts()
        assert counts[2] == 1 and counts[3] == 1 and counts[4] == 0
        assert m.focus_score() == 91  # 100 - 3 (L2) - 6 (L3)

    def test_pausa_tiene_franquicia_y_despues_castiga(self):
        libre = TestFocusSummary._with_work(_make_monitor(), 60)
        libre._pomodoro_total_pauses_sec = 5 * 60
        assert libre.focus_score() == 100

        pasado = TestFocusSummary._with_work(_make_monitor(), 60)
        pasado._pomodoro_total_pauses_sec = 10 * 60
        assert pasado.focus_score() == 90  # 5 min de exceso * 2

    def test_score_nunca_sale_del_rango(self):
        m = TestFocusSummary._with_work(_make_monitor(), 60, [(0, 59, "game")])
        for _ in range(12):
            m._distraction_log.append({
                "app": "x", "url": "", "started_at": 0.0, "ended_at": 0.0,
                "duration_sec": 0.0, "max_alert_level": 4, "enforced": True,
                "category": "game",
            })
        assert m.focus_score() == 0


# ═══════════════════════════════════════════════════════════════════
# 11. ScheduleSkill — extend heuristic (current vs new-cycle)
# ═══════════════════════════════════════════════════════════════════

class TestExtendHeuristic:
    """The skill picks 'current' vs 'new_cycle' based on remaining minutes
    and the requested amount, escalating to a confirmation prompt only in the
    ambiguous middle zone."""

    @pytest.fixture
    def ctx_admin(self):
        return SimpleNamespace(user_role="admin")

    async def _drive(self, skill, monitor_stub, text, ctx):
        # Patch get_focus_monitor in the skill module to return our stub.
        from skills.builtin.schedule import skill as skill_mod
        with patch.object(skill_mod, "get_focus_monitor", return_value=monitor_stub, create=True):
            # The skill imports get_focus_monitor inline inside _handle_pomodoro,
            # so patch the source it imports from too.
            from modules.schedule import monitor as monitor_mod
            with patch.object(monitor_mod, "get_focus_monitor", return_value=monitor_stub):
                return await skill._handle_pomodoro(text.lower(), ctx)

    async def test_short_remaining_extends_directly(self, skill, ctx_admin):
        monitor = MagicMock()
        monitor._pomodoro_task = _fake_running_task()
        monitor._pomodoro_remaining_min = MagicMock(return_value=3)
        monitor.extend_pomodoro = AsyncMock(return_value="Extiendo el ciclo actual 10 min más.")
        result = await self._drive(skill, monitor, "añade 10 minutos", ctx_admin)
        monitor.extend_pomodoro.assert_awaited_once_with(10, mode="current")
        assert "extiendo" in result.lower()

    async def test_large_minutes_extends_directly(self, skill, ctx_admin):
        """Asking for ≥25 min is treated as a full cycle request — extend now
        without the confirmation detour."""
        monitor = MagicMock()
        monitor._pomodoro_task = _fake_running_task()
        monitor._pomodoro_remaining_min = MagicMock(return_value=20)
        monitor.extend_pomodoro = AsyncMock(return_value="Extiendo el ciclo actual 25 min más.")
        result = await self._drive(skill, monitor, "añade 25 minutos", ctx_admin)
        monitor.extend_pomodoro.assert_awaited_once_with(25, mode="current")

    async def test_ambiguous_zone_asks_for_confirmation(self, skill, ctx_admin):
        monitor = MagicMock()
        monitor._pomodoro_task = _fake_running_task()
        monitor._pomodoro_remaining_min = MagicMock(return_value=15)
        monitor.extend_pomodoro = AsyncMock()
        result = await self._drive(skill, monitor, "añade 10 minutos", ctx_admin)
        # In ambiguous zone we don't extend yet — we stash the request and ask.
        monitor.extend_pomodoro.assert_not_called()
        assert "¿extender este ciclo" in result.lower() or "ciclo nuevo" in result.lower()
        # The pending state should hold the minutes for the follow-up turn.
        assert skill._pending_pomodoro_extend.get("admin") == 10

    async def test_no_active_session_message(self, skill, ctx_admin):
        monitor = MagicMock()
        monitor._pomodoro_task = None
        monitor._pomodoro_remaining_min = MagicMock(return_value=0)
        monitor.extend_pomodoro = AsyncMock()
        result = await self._drive(skill, monitor, "añade 10 minutos", ctx_admin)
        monitor.extend_pomodoro.assert_not_called()
        assert "no hay pomodoro activo" in result.lower()


# ═══════════════════════════════════════════════════════════════════
# 12. _schedule_llm_hint — strict (keyword AND temporal)
# ═══════════════════════════════════════════════════════════════════

from skills.builtin.schedule.skill import _schedule_llm_hint


class TestScheduleLlmHintStrict:
    """The tightened hint requires BOTH a temporal marker AND an agenda
    keyword. Long off-topic inputs that happen to mention one of them
    no longer get false-matched into the schedule skill."""

    def test_keyword_plus_day(self):
        assert _schedule_llm_hint("tengo clase el lunes")

    def test_keyword_plus_time(self):
        assert _schedule_llm_hint("agenda la reunión a las 18:00")

    def test_keyword_plus_today(self):
        assert _schedule_llm_hint("entrega hoy del laboratorio")

    def test_keyword_alone_rejected(self):
        # "la tarea es una RedLand de dos niveles..." — single agenda keyword,
        # no temporal anchor → must not match.
        assert not _schedule_llm_hint(
            "la tarea es una redland de dos niveles con subredes"
        )

    def test_temporal_alone_rejected(self):
        # Day mentioned in passing without any agenda keyword.
        assert not _schedule_llm_hint(
            "el lunes pasado el router falló y rompió las rutas estáticas"
        )

    def test_trabajo_keyword_in_technical_context_rejected(self):
        # "trabajo" is in the keyword list, but a long technical sentence
        # without a temporal marker shouldn't be claimed by schedule.
        assert not _schedule_llm_hint(
            "el trabajo en redes implica configurar protocolos como rip versión 2"
        )

    def test_curso_alone_rejected(self):
        assert not _schedule_llm_hint("el curso de redes es muy denso")

    def test_explicit_agenda_command_matches(self):
        assert _schedule_llm_hint("agenda gym hoy 18:00")

    def test_curso_plus_weekday(self):
        assert _schedule_llm_hint("curso de cálculo los lunes")

    def test_examen_plus_tomorrow(self):
        assert _schedule_llm_hint("tengo examen mañana de física")


# ═══════════════════════════════════════════════════════════════════
# 13. FocusMonitor — JARVIS call mode signal → Discord grace
# ═══════════════════════════════════════════════════════════════════

class TestJarvisCallModeGrace:
    """When set_jarvis_call_mode(True), Discord/messaging classifications
    redirect to the 'messaging_during_call' category (180s grace) instead
    of the harsher 30s 'messaging' grace."""

    def test_default_off(self):
        m = _make_monitor()
        assert m._jarvis_call_active is False

    def test_set_toggles(self):
        m = _make_monitor()
        m.set_jarvis_call_mode(True)
        assert m._jarvis_call_active is True
        m.set_jarvis_call_mode(False)
        assert m._jarvis_call_active is False

    def test_discord_normal_grace_off_call(self):
        m = _make_monitor()
        m.set_jarvis_call_mode(False)
        is_distraction, category = m._classify_distraction("Discord — #general", "")
        assert is_distraction is True
        assert category == "app"
        assert m._get_grace_period(category) == 0  # default_distraction

    def test_discord_relaxed_grace_during_call(self):
        m = _make_monitor()
        m.set_jarvis_call_mode(True)
        is_distraction, category = m._classify_distraction("Discord — #general", "")
        assert is_distraction is True
        assert category == "messaging_during_call"
        assert m._get_grace_period(category) == 180

    def test_whatsapp_relaxed_grace_during_call(self):
        m = _make_monitor()
        m.set_jarvis_call_mode(True)
        is_distraction, category = m._classify_distraction("WhatsApp", "")
        assert is_distraction is True
        assert category == "messaging_during_call"

    def test_whatsapp_normal_grace_off_call(self):
        m = _make_monitor()
        m.set_jarvis_call_mode(False)
        is_distraction, category = m._classify_distraction("WhatsApp", "")
        assert category == "messaging"
        assert m._get_grace_period(category) == 30

    def test_non_messaging_app_unaffected_by_call_mode(self):
        m = _make_monitor()
        m.set_jarvis_call_mode(True)
        # Steam should still be a regular distraction even during a call.
        is_distraction, category = m._classify_distraction("Steam", "")
        assert is_distraction is True
        assert category == "app"

    def test_whitelist_still_wins_during_call(self):
        """Even with relaxed Discord grace, real whitelist matches return
        non-distraction. GitHub and Canvas should stay neutral."""
        m = _make_monitor()
        m.set_jarvis_call_mode(True)
        is_distraction, _ = m._classify_distraction("GitHub", "https://github.com/user/repo")
        assert is_distraction is False


# ═══════════════════════════════════════════════════════════════════
# 14. Whitelist URLs — work/study sites
# ═══════════════════════════════════════════════════════════════════

class TestWorkUrlWhitelist:
    """Newly-added work-related domains should never count as distractions."""

    def test_github_whitelisted(self):
        m = _make_monitor()
        is_distraction, _ = m._classify_distraction(
            "GitHub", "https://github.com/anthropics/claude-code"
        )
        assert is_distraction is False

    def test_stackoverflow_whitelisted(self):
        m = _make_monitor()
        is_distraction, _ = m._classify_distraction(
            "Stack Overflow", "https://stackoverflow.com/questions/12345"
        )
        assert is_distraction is False

    def test_mdn_whitelisted(self):
        m = _make_monitor()
        is_distraction, _ = m._classify_distraction(
            "MDN", "https://developer.mozilla.org/en-US/docs/Web/CSS"
        )
        assert is_distraction is False

    def test_canvas_instructure_whitelisted(self):
        m = _make_monitor()
        is_distraction, _ = m._classify_distraction(
            "Canvas — Course", "https://ucsur.instructure.com/courses/123"
        )
        assert is_distraction is False

    def test_youtube_watch_still_distraction(self):
        # Regression guard: we expanded the whitelist but didn't accidentally
        # let entertainment YouTube through.
        m = _make_monitor()
        is_distraction, category = m._classify_distraction(
            "YouTube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        assert is_distraction is True
        assert category == "browser"
