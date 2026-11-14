"""Verifica el comportamiento del log reader en modo diagnóstico."""

import pytest
import re
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path


# ── Triggers de diagnóstico ──────────────────────────────────────────

DIAGNOSIS_TRIGGERS = [
    "revisa los logs para ver qué pasó",
    "revisa los logs para ver qué falló",
    "qué falló en los logs",
    "diagnóstico del sistema",
    "analiza los logs",
    "por qué falló el sistema",
    "qué pasó con el backend",
    "qué sucedió",
    "revisa logs del sistema",
    "revisa los logs del sistema",
]


@pytest.mark.parametrize("user_input", DIAGNOSIS_TRIGGERS)
def test_wants_diagnosis_detects_all_triggers(user_input):
    """Todos los triggers de diagnóstico deben ser detectados."""
    from skills.builtin.log_reader.skill import _wants_diagnosis
    assert _wants_diagnosis(user_input), \
        f"'{user_input}' debería activar _wants_diagnosis"


# ── No triggers de diagnóstico ───────────────────────────────────────

NON_DIAGNOSIS_INPUTS = [
    "dame los logs",
    "muéstrame los logs",
    "/logs backend.log",
    "ver logs",
    "últimos logs",
    "errores recientes",
]


@pytest.mark.parametrize("user_input", NON_DIAGNOSIS_INPUTS)
def test_normal_log_requests_not_diagnosis(user_input):
    """Peticiones normales de logs no deben activar diagnóstico."""
    from skills.builtin.log_reader.skill import _wants_diagnosis
    assert not _wants_diagnosis(user_input), \
        f"'{user_input}' NO debería activar _wants_diagnosis"


# ── has_errors ───────────────────────────────────────────────────────

def test_has_errors_detects_error_lines():
    from skills.builtin.log_reader.skill import LogReaderSkill
    assert LogReaderSkill._has_errors("ERROR something broke")
    assert LogReaderSkill._has_errors("CRITICAL failure")
    assert LogReaderSkill._has_errors("FATAL: out of memory")
    assert LogReaderSkill._has_errors("Traceback (most recent call last):")
    assert LogReaderSkill._has_errors("Exception: division by zero")
    assert LogReaderSkill._has_errors("connection fail")


def test_has_errors_ignores_clean_logs():
    from skills.builtin.log_reader.skill import LogReaderSkill
    assert not LogReaderSkill._has_errors("INFO: All systems nominal")
    assert not LogReaderSkill._has_errors("DEBUG: Processing request")
    assert not LogReaderSkill._has_errors("HTTP Request: POST https://api.telegram.org")


# ── Filtro de ruido ─────────────────────────────────────────────────

def test_noise_filter_removes_telegram_getupdates():
    """Líneas de Telegram getUpdates deben ser filtradas en diagnóstico."""
    from skills.builtin.log_reader.skill import _NOISE_RE

    noisy_log = (
        "httpx: HTTP Request: POST https://api.telegram.org/bot123/getUpdates\n"
        "ERROR: Discord connection failed\n"
        "apscheduler.executors.default: Running job"
    )

    lines = noisy_log.split("\n")
    filtered = [
        line for line in lines
        if not _NOISE_RE.search(line)
        or re.search(r'\b(?:ERROR|CRITICAL|FATAL|Traceback|Exception)\b', line)
    ]

    assert len(filtered) == 1
    assert "Discord connection failed" in filtered[0]
    assert "telegram" not in filtered[0].lower()


def test_noise_filter_preserves_error_lines():
    """Líneas con ERROR no deben filtrarse aunque matcheen ruido."""
    # Es improbable que ambas condiciones coincidan, pero el código lo soporta
    from skills.builtin.log_reader.skill import _NOISE_RE

    noisy_error = "ERROR apscheduler.executors.default: Running job failed"
    lines = [noisy_error]
    filtered = [
        line for line in lines
        if not _NOISE_RE.search(line)
        or re.search(r'\b(?:ERROR|CRITICAL|FATAL|Traceback|Exception)\b', line)
    ]
    assert len(filtered) == 1, "Lineas ERROR no deben filtrarse"


# ── BASE skill tiene set_llm_manager ─────────────────────────────────

def test_base_skill_has_set_llm_manager():
    """BaseSkill debe exponer set_llm_manager."""
    from skills.base import BaseSkill, SkillMetadata

    meta = SkillMetadata(name="test", description="test", triggers=["test"])

    # Crear subclase concreta mínima
    class _TestSkill(BaseSkill):
        async def handle(self, user_input, ctx):
            return None

    skill = _TestSkill(meta, Path("."))

    assert hasattr(skill, 'set_llm_manager'), "BaseSkill debe tener set_llm_manager"

    mock_llm = MagicMock()
    skill.set_llm_manager(mock_llm)
    assert skill.llm_manager is mock_llm


# ── Respetar _MAX_TAIL_LINES ─────────────────────────────────────────

def test_tail_respects_max_lines():
    """_MAX_TAIL_LINES debe ser 500 y _DEFAULT_TAIL_LINES debe ser 50."""
    from skills.builtin.log_reader.skill import _MAX_TAIL_LINES, _DEFAULT_TAIL_LINES
    assert _MAX_TAIL_LINES == 500
    assert _DEFAULT_TAIL_LINES == 50
