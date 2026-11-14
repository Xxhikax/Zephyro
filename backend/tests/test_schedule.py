"""Tests for schedule skill time parsing + service CRUD."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import time

import pytest

from modules.schedule.service import ScheduleService, get_schedule_service


# ══════════════════════════════════════════════════════════════════
# Time parsing tests (import from skill module)
# ══════════════════════════════════════════════════════════════════

from skills.builtin.schedule.skill import (
    _parse_hour_token,
    _parse_time_range,
    _parse_target_date,
    _ambiguity_note,
    _extract_title,
    _clean_title,
    _is_title_suspicious,
    _parse_schedule_llm_json,
    _SCHEDULE_LLM_HINT,
    _SEMANTIC_PATTERN_A,
    _SEMANTIC_PATTERN_B,
    _MODIFY_PATTERN,
    _DELETE_PATTERN,
    _EVENT_REF_PATTERN,
)


class TestParseHourToken:
    def test_hour_only(self):
        assert _parse_hour_token("7") == (7, 0)
        assert _parse_hour_token("15") == (15, 0)

    def test_hh_colon_mm(self):
        assert _parse_hour_token("7:30") == (7, 30)
        assert _parse_hour_token("15:45") == (15, 45)

    def test_named_minutes(self):
        assert _parse_hour_token("7 y media") == (7, 30)
        assert _parse_hour_token("7 y cuarto") == (7, 15)
        assert _parse_hour_token("7 y treinta") == (7, 30)
        assert _parse_hour_token("7 y quince") == (7, 15)
        assert _parse_hour_token("5 y cuarenta y cinco") == (5, 45)

    def test_numeric_minutes_bugfix(self):
        assert _parse_hour_token("11 y 10") == (11, 10)
        assert _parse_hour_token("7 y 20") == (7, 20)
        assert _parse_hour_token("3 y 45") == (3, 45)
        assert _parse_hour_token("9 y 5") == (9, 5)
        assert _parse_hour_token("12 y 15") == (12, 15)

    def test_numeric_minutes_with_am_pm(self):
        assert _parse_hour_token("7 y 10 am") == (7, 10)
        assert _parse_hour_token("3 y 30 pm") == (15, 30)

    def test_with_con(self):
        assert _parse_hour_token("7 con 10") == (7, 10)
        assert _parse_hour_token("7 con media") == (7, 30)

    def test_am_pm_basic(self):
        assert _parse_hour_token("7 am") == (7, 0)
        assert _parse_hour_token("12 pm") == (12, 0)
        assert _parse_hour_token("3 pm") == (15, 0)

    def test_edge_cases(self):
        assert _parse_hour_token("") is None
        assert _parse_hour_token("abc") is None
        # Fase 1B: horas inválidas (>23) ahora se rechazan, no se hacen wrap con mod 24.
        # Esto previene que "25:00" se acepte silenciosamente como "1:00".
        assert _parse_hour_token("25") is None
        assert _parse_hour_token("24") is None
        assert _parse_hour_token("99") is None


class TestParseTimeRange:
    def test_range_de_a(self):
        start, end = _parse_time_range("de 7 a 9")
        assert start == (7, 0)
        assert end == (9, 0)

    def test_range_with_named_minutes(self):
        start, end = _parse_time_range("de 7 de la mañana hasta las 9 y media")
        assert start == (7, 0)
        assert end == (9, 30)

    def test_range_with_numeric_minutes_bugfix(self):
        start, end = _parse_time_range("de 7 a 11 y 10")
        assert start == (7, 0)
        assert end == (11, 10)

    def test_range_hasta_numeric_minutes(self):
        start, end = _parse_time_range("hasta las 11 y 10")
        assert start == (11, 10)
        assert end is None

    def test_single_time(self):
        start, end = _parse_time_range("a las 8")
        assert start == (8, 0)
        assert end is None

    def test_no_time(self):
        start, end = _parse_time_range("sin horario")
        assert start is None
        assert end is None


class TestParseTargetDate:
    def test_hoy(self):
        from datetime import datetime, time as dtime
        result = _parse_target_date("hoy")
        assert result is not None
        assert result.date() == datetime.now().date()

    def test_manana(self):
        from datetime import datetime, time as dtime, timedelta
        result = _parse_target_date("mañana")
        assert result is not None
        assert result.date() == (datetime.now() + timedelta(days=1)).date()

    def test_no_match(self):
        assert _parse_target_date("algún día") is None


class TestAmbiguityNote:
    def test_no_ambiguity(self):
        assert _ambiguity_note("clases de sistemas operativos", "university", (10, 30), 1) == ""

    def test_generic_title(self):
        result = _ambiguity_note("clase", "university", (9, 30), 1)
        assert "título genérico" in result
        assert "modifica evento 1" in result

    def test_other_category(self):
        result = _ambiguity_note("reunión", "other", (9, 0), 1)
        assert "categoría sin definir" in result
        assert "modifica evento 1" in result

    def test_assumed_end_time(self):
        result = _ambiguity_note("gym", "exercise", None, 1)
        assert "hora de fin asumida" in result
        assert "modifica evento 1" in result

    def test_multiple_warnings(self):
        result = _ambiguity_note("clase", "other", None, 5)
        assert "título genérico" in result
        assert "categoría sin definir" in result
        assert "hora de fin asumida" in result
        assert "modifica evento 5" in result


# ══════════════════════════════════════════════════════════════════
# ScheduleService CRUD tests
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def service():
    s = ScheduleService(db_path="./storage/test_schedule.db")
    # Clean up any leftover data
    import sqlite3
    with sqlite3.connect(s._db_path) as conn:
        conn.execute("DELETE FROM schedule_events")
        conn.commit()
    yield s
    # Cleanup
    import os
    try:
        os.remove(s._db_path)
    except OSError:
        pass


class TestScheduleServiceCRUD:
    def test_create_event(self, service):
        ev = service.create_event(
            user_id="test_user",
            title="Clase de prueba",
            category="university",
            start_ts_utc=time.time() + 3600,
            end_ts_utc=time.time() + 7200,
        )
        assert ev.id is not None
        assert ev.title == "Clase de prueba"
        assert ev.category == "university"

    def test_create_event_invalid_category(self, service):
        ev = service.create_event(
            user_id="test_user",
            title="Evento inválido",
            category="no_existe",
            start_ts_utc=time.time() + 3600,
            end_ts_utc=time.time() + 7200,
        )
        assert ev.category == "other"

    def test_create_event_end_before_start(self, service):
        ev = service.create_event(
            user_id="test_user",
            title="Mal rango",
            category="other",
            start_ts_utc=time.time() + 3600,
            end_ts_utc=time.time() + 1800,  # antes que start
        )
        assert ev.end_ts_utc == ev.start_ts_utc + 3600

    def test_get_events_in_range(self, service):
        now = time.time()
        service.create_event("u1", "A", "other", now + 1000, now + 2000)
        service.create_event("u1", "B", "other", now + 5000, now + 6000)
        service.create_event("u1", "C", "other", now + 10000, now + 11000)

        events = service.get_events_in_range("u1", now, now + 7000)
        assert len(events) == 2

    def test_delete_event(self, service):
        ev = service.create_event("u1", "Para borrar", "other", time.time() + 1000, time.time() + 2000)
        assert service.delete_event(ev.id) is True
        assert service.delete_event(99999) is False

    def test_update_event_title(self, service):
        ev = service.create_event("u1", "Original", "other", time.time() + 1000, time.time() + 2000)
        updated = service.update_event(ev.id, title="Modificado")
        assert updated is not None
        assert updated.title == "Modificado"
        assert updated.category == "other"  # unchanged

    def test_update_event_category(self, service):
        ev = service.create_event("u1", "X", "other", time.time() + 1000, time.time() + 2000)
        updated = service.update_event(ev.id, category="exercise")
        assert updated is not None
        assert updated.category == "exercise"

    def test_update_event_time(self, service):
        now = time.time()
        ev = service.create_event("u1", "X", "other", now + 1000, now + 2000)
        new_start = now + 5000
        updated = service.update_event(ev.id, start_ts_utc=new_start)
        assert updated is not None
        assert updated.start_ts_utc == new_start

    def test_update_event_noop(self, service):
        result = service.update_event(99999, title="No existe")
        assert result is None

    def test_get_event_by_id(self, service):
        ev = service.create_event("u1", "Buscar", "other", time.time() + 1000, time.time() + 2000)
        found = service.get_event_by_id(ev.id)
        assert found is not None
        assert found.title == "Buscar"

        not_found = service.get_event_by_id(99999)
        assert not_found is None

    def test_to_dict(self, service):
        ev = service.create_event("u1", "Dict test", "university", time.time() + 1000, time.time() + 2000, notes="aula 101")
        d = ev.to_dict()
        assert d["title"] == "Dict test"
        assert d["category"] == "university"
        assert d["notes"] == "aula 101"
        assert "start_iso" in d
        assert "end_iso" in d


# ══════════════════════════════════════════════════════════════════
# Semantic pattern matching tests (fix 2: verbos + bidireccional)
# ══════════════════════════════════════════════════════════════════

class TestSemanticPatterns:
    """Verifica que los patrones semánticos matchean frases reales del usuario."""

    def test_pattern_a_verb_first(self):
        assert _SEMANTIC_PATTERN_A.search("agenda clase de cálculo el lunes de 7 a 9")
        assert _SEMANTIC_PATTERN_A.search("pon clase de sistemas operativos el lunes de 7 a 11")
        assert _SEMANTIC_PATTERN_A.search("agenda gym hoy a las 18")

    def test_pattern_b_day_first(self):
        assert _SEMANTIC_PATTERN_B.search("el lunes agenda un curso de 7 a 11")
        assert _SEMANTIC_PATTERN_B.search("mañana ponme clase de cálculo a las 8")
        assert _SEMANTIC_PATTERN_B.search("el día lunes agenda un horario de 7")

    def test_subjunctive_verbs(self):
        fallback = (r"pon|agenda|agregar")  # minimal verbs
        import re
        verbs_re = re.compile(
            r"agendes|agende|agenden|pongas|ponga|pongan|anotes|anote|anoten|"
            r"a[ñn]adas|a[ñn]ada|a[ñn]adan",
            re.IGNORECASE,
        )
        assert verbs_re.search("necesito que agendes")
        assert verbs_re.search("quiero que pongas")
        assert verbs_re.search("agendes")  # solo
        assert verbs_re.search("pongas")   # solo

    def test_real_user_input_1(self):
        text = "el día lunes de mañana agenda un horario de un curso que se llama sistemas operativos"
        assert _SEMANTIC_PATTERN_B.search(text.lower()) is None  # no time → no match

    def test_real_user_input_1_with_time(self):
        text = "el día lunes de mañana agenda un horario de un curso de sistemas operativos de 7 de la mañana"
        assert _SEMANTIC_PATTERN_B.search(text.lower()) is not None  # has time → match

    def test_real_user_input_2(self):
        text = "para el día lunes de mañana necesito que agendes un curso de sistemas operativos de 7 de la mañana hasta las 11 y 10"
        assert _SEMANTIC_PATTERN_B.search(text.lower())

    def test_real_user_input_3(self):
        text = "necesito que agendes sistemas operativos el lunes de 7 a 11"
        assert _SEMANTIC_PATTERN_A.search(text.lower())

    def test_modify_detect(self):
        assert _MODIFY_PATTERN.search("modifica el evento 13")
        assert _MODIFY_PATTERN.search("cambia la categoría del evento 13")
        assert not _MODIFY_PATTERN.search("agenda clase mañana")

    def test_delete_detect(self):
        assert _DELETE_PATTERN.search("elimina el evento 13")
        assert _DELETE_PATTERN.search("borra la clase de cálculo")
        assert not _DELETE_PATTERN.search("agenda clase mañana")


class TestEventoNumeroTolerance:
    """Bug en vivo (2026-07-02): "Elimina el evento número 97" no era reconocido
    porque los regex de ID solo toleraban espacios/# entre "evento" y el dígito.
    """

    _ID_RE = r"\bevento\s*(?:(?:n[úu]mero|n[úu]m\.?|nro\.?|#)\s*)?(\d+)"

    def test_event_ref_with_numero(self):
        assert _EVENT_REF_PATTERN.search("modifica el evento número 13")
        assert _EVENT_REF_PATTERN.search("elimina el evento nro. 97")

    def test_event_ref_bare_still_works(self):
        assert _EVENT_REF_PATTERN.search("modifica evento 13")
        assert _EVENT_REF_PATTERN.search("elimina evento #13")

    @pytest.mark.parametrize("text,expected_id", [
        ("elimina el evento número 97", 97),
        ("elimina evento nro. 13", 13),
        ("elimina evento núm. 13", 13),
        ("elimina evento 13", 13),
        ("modifica el evento # 5, hora fin 11:10", 5),
    ])
    def test_id_extraction_with_number_words(self, text, expected_id):
        import re as _re
        m = _re.search(self._ID_RE, text.lower())
        assert m and int(m.group(1)) == expected_id

    def test_cleanup_regex_removes_numero_too(self):
        # El re.sub de _handle_update debe borrar la mención completa, o
        # "número" queda suelto en el texto que va al parser de fechas.
        import re as _re
        cleaned = _re.sub(
            r"\bevento\s*(?:(?:n[úu]mero|n[úu]m\.?|nro\.?|#)\s*)?\d+",
            "", "modifica el evento número 13 para que termine a las 11:10",
        )
        assert "número" not in cleaned and "13" not in cleaned


# ══════════════════════════════════════════════════════════════════
# Cross-turn buffer tests
# ══════════════════════════════════════════════════════════════════

class TestCrossTurnBuffer:
    """Verifica el patrón de detección de continuaciones para cross-turn."""
    import re

    _IS_CONTINUATION = re.compile(
        r'^(?:de|a|las|y|con|hasta|desde|por|para|en|del)\s+\w+'
        r'|^\d{1,2}(?:\s*(?:y|con)\s*\d{1,2})?(?:\s*(?:am|pm|de\s+la\s+\w+))?'
        r'|^(?:de|a)\s+la\s+\w+'
        r'|^(?:de|a)\s+las?\s+\d',
        re.IGNORECASE,
    )

    def test_continuation_time(self):
        assert self._IS_CONTINUATION.match("de 7 de la mañana hasta las 11 y 10")
        assert self._IS_CONTINUATION.match("de siete")
        assert self._IS_CONTINUATION.match("hasta las 11 y 10")
        assert self._IS_CONTINUATION.match("a las 8")

    def test_continuation_numbers(self):
        assert self._IS_CONTINUATION.match("7 de la mañana")
        assert self._IS_CONTINUATION.match("11 y 10")

    def test_not_continuation(self):
        assert not self._IS_CONTINUATION.match("hola")
        assert not self._IS_CONTINUATION.match("sí")
        assert not self._IS_CONTINUATION.match("no")
        assert not self._IS_CONTINUATION.match("gracias")
        assert not self._IS_CONTINUATION.match("agenda clase mañana a las 8")


# ══════════════════════════════════════════════════════════════════
# Title extraction tests (fix 1: "llamado X", "por favor", etc.)
# ══════════════════════════════════════════════════════════════════

class TestExtractTitle:
    """Verifica extracción de títulos desde frases naturales."""

    def test_llamado_pattern(self):
        result = _extract_title(
            "Por favor, para el día de mañana, agéndame. Tengo un curso "
            "llamado Sistemas Operativos de 7 de la mañana hasta las 11 y 10."
        )
        assert result == "Sistemas Operativos"

    def test_se_llama_pattern(self):
        result = _extract_title(
            "agéndame un curso que se llama Cálculo III el lunes de 8 a 10"
        )
        assert result == "Cálculo III"

    def test_que_tengo_pattern(self):
        result = _extract_title(
            "pon el lunes que tengo clases de sistemas operativos de 7 a 9:30"
        )
        # New pattern "tengo clases? de X" matches first and strips "clases de",
        # yielding just the course name without the generic descriptor.
        assert result == "sistemas operativos"

    def test_llamado_with_articles(self):
        result = _extract_title(
            "tengo un curso llamado el arte de la programación de 9 a 11"
        )
        assert result == "arte de la programación"

    def test_simple_agenda(self):
        result = _extract_title("agenda gym hoy 23:00")
        assert result == "gym"

    def test_agenda_en_mi_agenda(self):
        result = _extract_title("pon en mi agenda clase de cálculo el lunes de 7 a 9:30")
        assert result == "clase de cálculo"

    def test_missing_preposition_mi_agenda(self):
        # Bug en vivo (2026-07-02): "añade mi agenda el curso..." (sin la
        # preposición "a") producía el título "mi el curso de diseño de datos".
        # El envoltorio "curso de" también se quita: el título es la materia.
        result = _extract_title(
            "Zephyro, añade mi agenda el curso de diseño de datos el lunes de 7 a 9"
        )
        assert result == "diseño de datos"

    def test_curso_de_wrapper_stripped(self):
        result = _extract_title(
            "pon en mi agenda el curso de lenguaje de programación el martes a las 11"
        )
        assert result == "lenguaje de programación"

    def test_missing_preposition_tu_agenda(self):
        result = _extract_title("pon tu agenda examen de física el martes de 3 a 5")
        assert result == "examen de física"


class TestSuspiciousPossessiveArticle:
    """Safety net: "mi el X" nunca es un título válido → debe dispararse el
    fallback LLM en vez de crear el evento con título basura."""

    def test_mi_el_flagged(self):
        assert _is_title_suspicious("mi el curso de diseño de datos") is True

    def test_valid_titles_not_flagged(self):
        assert _is_title_suspicious("curso de diseño de datos") is False
        assert _is_title_suspicious("mi entrenamiento de boxeo") is False


# ══════════════════════════════════════════════════════════════════
# Ambiguity note tests (enhanced: garbage titles)
# ══════════════════════════════════════════════════════════════════

class TestAmbiguityNoteEnhanced:
    """Verifica detección de títulos basura."""

    def test_garbage_title_por_favor(self):
        result = _ambiguity_note("Por favor, para el día de", "other", None, 1)
        assert "título dudoso" in result
        assert "categoría sin definir" in result
        assert "hora de fin asumida" in result
        assert "modifica evento 1" in result

    def test_agenda_as_title(self):
        result = _ambiguity_note("agenda", "other", None, 2)
        assert "título dudoso" in result

    def test_short_title(self):
        result = _ambiguity_note("abc", "other", None, 3)
        assert "título dudoso" in result

    def test_generic_title_still_works(self):
        result = _ambiguity_note("clase", "university", (9, 30), 4)
        assert "título genérico" in result

    def test_clean_title_no_warning(self):
        result = _ambiguity_note("Sistemas Operativos", "university", (11, 10), 5)
        assert result == ""


# ══════════════════════════════════════════════════════════════════
# Clean title tests
# ══════════════════════════════════════════════════════════════════

class TestCleanTitle:
    """Verifica limpieza de títulos residuales."""

    def test_removes_agendame(self):
        assert _clean_title("agéndame. Sistemas Operativos") == "Sistemas Operativos"
        assert _clean_title("agenda Sistemas Operativos") == "Sistemas Operativos"

    def test_removes_tengo(self):
        assert _clean_title("tengo Sistemas Operativos") == "Sistemas Operativos"
        assert _clean_title("necesito que Sistemas Operativos") == "Sistemas Operativos"

    def test_collapses_spaces(self):
        assert _clean_title("  Sistemas   Operativos  ") == "Sistemas Operativos"


# ══════════════════════════════════════════════════════════════════
# Schedule LLM extraction tests
# ══════════════════════════════════════════════════════════════════

class TestScheduleLLMHint:
    """Verifica que _SCHEDULE_LLM_HINT detecta comandos de agenda sueltos."""

    def test_matches_day_and_time(self):
        assert _SCHEDULE_LLM_HINT.search("mañana a las 7")
        assert _SCHEDULE_LLM_HINT.search("el lunes de 7 a 11")
        assert _SCHEDULE_LLM_HINT.search("hoy 11 y 10")

    def test_matches_schedule_keywords(self):
        assert _SCHEDULE_LLM_HINT.search("agenda")
        assert _SCHEDULE_LLM_HINT.search("agéndame")
        assert _SCHEDULE_LLM_HINT.search("horario")
        assert _SCHEDULE_LLM_HINT.search("calendario")
        assert _SCHEDULE_LLM_HINT.search("tengo clase")
        assert _SCHEDULE_LLM_HINT.search("tengo curso")

    def test_matches_loose_phrasing(self):
        assert _SCHEDULE_LLM_HINT.search("me gustaría guardar en mi agenda la clase de mañana")
        assert _SCHEDULE_LLM_HINT.search("quiero anotar que tengo curso el miércoles a las 10")
        assert _SCHEDULE_LLM_HINT.search("anótame para mañana la clase de cálculo")

    def test_does_not_match_random_text(self):
        assert not _SCHEDULE_LLM_HINT.search("hola cómo estás")
        assert not _SCHEDULE_LLM_HINT.search("gracias")
        assert not _SCHEDULE_LLM_HINT.search("qué es python")


class TestParseScheduleLLMJson:
    """Verifica el parseo de JSON del LLM para agenda."""

    def test_create_action(self):
        result = _parse_schedule_llm_json(
            '{"action": "create", "title": "Sistemas Operativos", '
            '"date_keyword": "lunes", "start_hour": 7, "start_minute": 0, '
            '"end_hour": 11, "end_minute": 10, "category": "university"}'
        )
        assert result is not None
        assert result["action"] == "create"
        assert result["title"] == "Sistemas Operativos"
        assert result["start_hour"] == 7
        assert result["end_minute"] == 10
        assert result["category"] == "university"

    def test_list_action(self):
        result = _parse_schedule_llm_json(
            '{"action": "list", "date_keyword": "hoy"}'
        )
        assert result is not None
        assert result["action"] == "list"

    def test_delete_action(self):
        result = _parse_schedule_llm_json(
            '{"action": "delete", "event_id": 13}'
        )
        assert result is not None
        assert result["action"] == "delete"
        assert result["event_id"] == 13

    def test_modify_action(self):
        result = _parse_schedule_llm_json(
            '{"action": "modify", "event_id": 5, "title": "Nuevo título"}'
        )
        assert result is not None
        assert result["action"] == "modify"

    def test_none_action(self):
        result = _parse_schedule_llm_json('{"action": "none"}')
        assert result is not None
        assert result["action"] == "none"

    def test_markdown_block(self):
        result = _parse_schedule_llm_json(
            '```json\n{"action": "create", "title": "Gym"}\n```'
        )
        assert result is not None
        assert result["title"] == "Gym"

    def test_invalid_json(self):
        result = _parse_schedule_llm_json("no soy json")
        assert result is None


class TestExtractTitleProximoDia:
    def test_strips_proximo_dia_lunes(self):
        t = _extract_title(
            "Para el próximo día lunes, dos exámenes, diseño de base de datos de 12 a 1 y 40"
        )
        assert "diseño de base de datos" in t.lower()

    def test_strips_dos_examenes_prefix(self):
        t = _extract_title("dos exámenes, cálculo de 9 a 11 el martes")
        assert "cálculo" in t.lower()

    def test_suspicious_temporal_title(self):
        assert _is_title_suspicious("Para el próximo día") is True
        assert _is_title_suspicious("el próximo lunes") is True
        assert _is_title_suspicious("Cálculo") is False
        assert _is_title_suspicious("Diseño de Base de Datos") is False


# ══════════════════════════════════════════════════════════════════
# New regression tests: wake-word strip, multi-sentence, "en punto"
# ══════════════════════════════════════════════════════════════════

class TestWakeWordStrip:
    """Verifica que el wake word 'Zephyro' se elimina antes de extraer el título."""

    def test_zephyro_wake_word_with_second_sentence(self):
        # Bug original: título quedaba "Zephyro, anote mi"
        result = _extract_title(
            "Zephyro, anote mi agenda el día de mañana a las 11 y 10 de la mañana "
            "hasta las 2 en punto de la tarde. Tengo clases de lenguajes de programación 1."
        )
        assert "zephyro" not in result.lower()
        assert "anote" not in result.lower()
        assert "lenguajes" in result.lower()

    def test_zephyro_without_second_sentence(self):
        # Wake word sin segunda oración — no debe dar título vacío ni con "Zephyro"
        result = _extract_title("Zephyro, agenda gym mañana a las 7")
        assert "zephyro" not in result.lower()
        assert result.strip() != ""
        assert "gym" in result.lower()

    def test_hey_zephyro(self):
        result = _extract_title("Hey Zephyro, pon reunión el lunes de 9 a 11")
        assert "zephyro" not in result.lower()
        assert "reunión" in result.lower() or "reunion" in result.lower()


class TestMultiSentenceTitulo:
    """Verifica extracción de título de la última oración cuando es 'Tengo X'."""

    def test_tengo_clases_second_sentence(self):
        result = _extract_title(
            "Anota para mañana de 8 a 10 de la mañana. Tengo clases de cálculo integral."
        )
        assert "cálculo" in result.lower() or "calculo" in result.lower()

    def test_tengo_es_solo_temporal_no_extrae(self):
        # "Tengo X el lunes" tiene marcador temporal → no debe extraerse como título suelto
        result = _extract_title(
            "agenda para mañana. Tengo reunión el lunes a las 9."
        )
        # No debe devolver solo "reunión el lunes a las 9" — debe ignorar el marcador temporal
        assert "lunes" not in result.lower() or "reunión" in result.lower()


class TestEnPuntoTimeParsing:
    """Verifica parseo de 'X en punto de la tarde/mañana'."""

    def test_2_en_punto_de_la_tarde(self):
        # Bug original: "2 en punto de la tarde" → 02:00 en lugar de 14:00
        start, end = _parse_time_range("a las 11 y 10 de la mañana hasta las 2 en punto de la tarde")
        assert start == (11, 10)
        assert end == (14, 0)

    def test_3_en_punto_de_la_tarde(self):
        start, end = _parse_time_range("a las 3 en punto de la tarde")
        assert start == (15, 0)

    def test_8_en_punto_de_la_manana(self):
        start, end = _parse_time_range("a las 8 en punto de la mañana hasta las 10")
        assert start == (8, 0)

    def test_en_punto_sin_periodo(self):
        # "3 en punto" sin especificar tarde/mañana → hora ambigua (3 AM)
        start, end = _parse_time_range("agenda reunión a las 3 en punto")
        assert start == (3, 0)  # sin período, queda como hora literal
