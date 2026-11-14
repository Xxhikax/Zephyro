# -*- coding: utf-8 -*-
"""
Regresión del cross-turn buffer de call mode (bug en vivo 2026-07-02).

"Para el día de mañana en mi agenda pon el curso de lenguaje de programación
a las 11 y 10..." arrancaba con preposición → _IS_CONTINUATION lo trataba como
fragmento de VAD y lo fusionaba con el buffer, que retenía el turno de CHAT
anterior ("explícame que es el open source") porque el buffer solo se limpia
cuando un skill matchea. Resultado: evento agendado con las horas correctas
pero titulado con la pregunta del turno anterior.

Guard nuevo: un fragmento real es corto (≤12 palabras) y sin verbo imperativo.
"""
from core.orchestrator import _is_call_turn_fragment


class TestRealFragmentsStillMerge:
    """Los fragmentos pineados en TestCrossTurnBuffer siguen siendo mergeables."""

    def test_time_fragments(self):
        assert _is_call_turn_fragment("de 7 de la mañana hasta las 11 y 10")
        assert _is_call_turn_fragment("de siete")
        assert _is_call_turn_fragment("hasta las 11 y 10")
        assert _is_call_turn_fragment("a las 8")

    def test_number_fragments(self):
        assert _is_call_turn_fragment("7 de la mañana")
        assert _is_call_turn_fragment("11 y 10")


class TestCompleteCommandsNotMerged:
    def test_live_bug_phrase(self):
        # 24 palabras + verbo "pon" → comando completo, jamás fragmento.
        assert not _is_call_turn_fragment(
            "Para el día de mañana en mi agenda pon el curso de lenguaje de "
            "programación a las 11 y 10 de la mañana hasta las 2 de la tarde."
        )

    def test_short_command_with_verb(self):
        # Corto y arranca con preposición, pero tiene verbo imperativo.
        assert not _is_call_turn_fragment("para mañana pon gym a las 8")
        assert not _is_call_turn_fragment("en la agenda anota examen a las 9")

    def test_non_continuations(self):
        assert not _is_call_turn_fragment("hola")
        assert not _is_call_turn_fragment("sí")
        assert not _is_call_turn_fragment("gracias")
        assert not _is_call_turn_fragment("agenda clase mañana a las 8")

    def test_empty(self):
        assert not _is_call_turn_fragment("")
        assert not _is_call_turn_fragment("   ")
