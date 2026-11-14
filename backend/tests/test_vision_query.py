"""Tests for the VisionQuerySkill match regex.

Covers `_VISION_QUERY_RE` defined in skills/builtin/vision_query/skill.py.

Run from backend/:
    pytest tests/test_vision_query.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.builtin.vision_query.skill import _VISION_QUERY_RE


class TestExplicitScreenReferences:
    """Direct, unambiguous requests for screen analysis."""

    def test_mira_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("mira mi pantalla")

    def test_ve_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("ve mi pantalla")

    def test_ve_la_pantalla(self):
        assert _VISION_QUERY_RE.search("ve la pantalla")

    def test_vea_mi_pantalla(self):
        # Subjuntivo formal — should still match.
        assert _VISION_QUERY_RE.search("vea mi pantalla por favor")

    def test_fijate_en_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("fíjate en mi pantalla")

    def test_checa_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("checa mi pantalla")

    def test_revisa_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("revisa mi pantalla")

    def test_analiza_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("analiza mi pantalla")

    def test_qué_ves_en_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("qué ves en mi pantalla")

    def test_que_ves_sin_acento(self):
        assert _VISION_QUERY_RE.search("que ves en mi pantalla")

    def test_qué_hay_en_pantalla(self):
        assert _VISION_QUERY_RE.search("qué hay en la pantalla")

    def test_qué_tengo_en_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("qué tengo en mi pantalla")

    def test_qué_aparece_en_pantalla(self):
        assert _VISION_QUERY_RE.search("qué aparece en pantalla")

    def test_qué_se_ve_en_pantalla(self):
        assert _VISION_QUERY_RE.search("qué se ve en mi pantalla")

    def test_describe_la_pantalla(self):
        assert _VISION_QUERY_RE.search("describe la pantalla")

    def test_describeme_lo_que_ves(self):
        assert _VISION_QUERY_RE.search("descríbeme lo que ves")

    def test_captura_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("captura mi pantalla")

    def test_puedes_ver_mi_pantalla(self):
        assert _VISION_QUERY_RE.search("puedes ver mi pantalla")

    def test_lee_lo_que_aparece(self):
        assert _VISION_QUERY_RE.search("lee lo que aparece")


class TestImplicitScreenReferences:
    """Deictic / contextual phrasings that mean 'analyze what's visible'."""

    def test_ayudame_con_esto(self):
        assert _VISION_QUERY_RE.search("ayúdame con esto")

    def test_ayuda_con_lo_que_ves(self):
        assert _VISION_QUERY_RE.search("ayuda con lo que ves")

    def test_ayuda_con_esto_que_tengo(self):
        assert _VISION_QUERY_RE.search("ayúdame con esto que tengo")

    def test_mira_esto(self):
        assert _VISION_QUERY_RE.search("mira esto")

    def test_mira_esto_que_tengo(self):
        assert _VISION_QUERY_RE.search("mira esto que tengo")

    def test_mira_esto_que_ves(self):
        assert _VISION_QUERY_RE.search("mira esto que ves")

    def test_qué_opinas_de_esto(self):
        assert _VISION_QUERY_RE.search("qué opinas de esto")

    def test_qué_opinas_sobre_lo_que_ves(self):
        assert _VISION_QUERY_RE.search("qué opinas sobre lo que ves")

    def test_ve_esto(self):
        assert _VISION_QUERY_RE.search("ve esto")

    def test_ve_esto_que_tengo(self):
        assert _VISION_QUERY_RE.search("ve esto que tengo")


class TestNonMatches:
    """Conversational text that should NOT trigger a vision capture."""

    def test_normal_question(self):
        assert not _VISION_QUERY_RE.search("¿cuál es la diferencia entre RIP y rutas estáticas?")

    def test_about_topology_without_screen(self):
        assert not _VISION_QUERY_RE.search(
            "la tarea es una RedLand de dos niveles con subredes"
        )

    def test_just_pantalla_noun(self):
        # The noun "pantalla" alone (e.g. "tengo dos pantallas") shouldn't match.
        assert not _VISION_QUERY_RE.search("tengo dos pantallas en mi escritorio")

    def test_qué_opinas_de_otro_tema(self):
        # "qué opinas de" without "esto/lo que ves" is generic.
        assert not _VISION_QUERY_RE.search("qué opinas de RIPv2 versus rutas estáticas")

    def test_general_greeting(self):
        assert not _VISION_QUERY_RE.search("hola, ¿cómo estás?")

    def test_pomodoro_command(self):
        assert not _VISION_QUERY_RE.search("inicia una sesión de pomodoro")

    def test_schedule_command(self):
        assert not _VISION_QUERY_RE.search("agenda gym hoy 18:00")

    def test_mira_alone_no_screen(self):
        # "mira" without "esto" / "mi pantalla" — generic interjection.
        assert not _VISION_QUERY_RE.search("mira, te explico")

    def test_ayuda_sin_esto(self):
        assert not _VISION_QUERY_RE.search("ayúdame con la tarea de matemáticas")


class TestCaseInsensitive:
    """Match should be case-insensitive."""

    def test_uppercase(self):
        assert _VISION_QUERY_RE.search("MIRA MI PANTALLA")

    def test_mixed_case(self):
        assert _VISION_QUERY_RE.search("Mira Mi Pantalla")

    def test_first_word_capital(self):
        assert _VISION_QUERY_RE.search("Qué ves en mi pantalla?")
