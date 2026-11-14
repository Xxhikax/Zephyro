"""Tests for log-reader skill: trigger matching + diagnostic detection."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from skills.builtin.log_reader.skill import _wants_diagnosis, _DIAGNOSTIC_HINT


class TestDiagnosticDetection:
    """Verifica que _wants_diagnosis detecta correctamente intención de diagnóstico."""

    def test_explicit_diagnosis_request(self):
        assert _wants_diagnosis("revisa los logs del sistema para ver qué falló")
        assert _wants_diagnosis("revisa los logs para ver qué pasó")
        assert _wants_diagnosis("analiza los logs")
        assert _wants_diagnosis("diagnostica el problema")
        assert _wants_diagnosis("haz un diagnóstico de los errores")

    def test_question_about_errors(self):
        assert _wants_diagnosis("qué falló")
        assert _wants_diagnosis("por qué falló")
        assert _wants_diagnosis("por qué no funcionó")
        assert _wants_diagnosis("qué pasó")
        assert _wants_diagnosis("qué sucedió")
        assert _wants_diagnosis("qué ocurrió")

    def test_not_diagnosis(self):
        assert not _wants_diagnosis("/logs")
        assert not _wants_diagnosis("ver logs")
        assert not _wants_diagnosis("verifica logs")
        assert not _wants_diagnosis("ultimos logs")
        assert not _wants_diagnosis("/logs errors")
        assert not _wants_diagnosis("/logs backend.log 100")

    def test_diagnostic_hint_regex(self):
        assert _DIAGNOSTIC_HINT.search("revisa los logs para ver qué falló")
        assert _DIAGNOSTIC_HINT.search("revisa los logs del sistema para ver qué pasó")
        assert _DIAGNOSTIC_HINT.search("analiza los logs")
        assert _DIAGNOSTIC_HINT.search("diagnostica el problema")
        assert not _DIAGNOSTIC_HINT.search("/logs")
        assert not _DIAGNOSTIC_HINT.search("ver logs")
