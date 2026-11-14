# -*- coding: utf-8 -*-
"""Canvas: un curso sin nota NO es un curso inexistente.

Bugs reales de una conversación de Bruno (2026-09-30):

- Preguntó por la nota de Diseño de Software y el asistente terminó afirmando que
  ese curso "está dentro de Sistema de Gestión de Base de Datos". Son cursos
  distintos. La raíz: `client.get_course_grades()` descartaba los cursos con
  `computed_current_score is None`, así que un curso sin calificar directamente no
  existía para la skill y fallaba como "no encontré ese curso" — dejándole el turno
  al LLM, que completó el hueco inventando.
- Entregas de un curso aparecían en el resumen de otro: `_names_overlap` hacía
  `"" in b` → True, y `get_missing_submissions()` deja `course=""` cuando el id no
  está en el mapa.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.builtin.canvas.skill import _norm, _significant_words  # noqa: E402
from skills.loader import SkillLoader  # noqa: E402
from skills.registry import SkillRegistry  # noqa: E402

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


# ── Stub: varios cursos, uno SIN nota, una entrega huérfana ──────────────────

class _StubClient:
    """Refleja la forma real de los datos, incluidos los casos molestos."""

    def is_configured(self):
        return True

    async def get_course_grades(self):
        return [
            {"course": "Estadística Aplicada", "course_id": 1, "score": 90.0, "grade": "A"},
            {"course": "Lenguajes de Programación II", "course_id": 2, "score": 90.0, "grade": "A"},
            {"course": "Sistema de Gestión de Base de Datos", "course_id": 3, "score": 30.0, "grade": "F"},
            # El curso del bug: existe, cursado, pero todavía sin nota publicada.
            {"course": "Diseño de Software", "course_id": 4, "score": None, "grade": None},
            # Ambigüedad real: "diseño" a secas no distingue entre estos dos.
            {"course": "Diseño de Interfaces", "course_id": 5, "score": 75.0, "grade": "B"},
        ]

    async def get_missing_submissions(self):
        return [
            {"title": "Tarea02", "course": "Lenguajes de Programación II", "due_at": None},
            # Entrega huérfana: Canvas no resolvió el curso. No es de nadie.
            {"title": "Entrega sin curso", "course": "", "due_at": None},
        ]

    async def get_announcements(self, limit=10):
        return []

    async def get_upcoming_assignments(self, days=7):
        return []

    async def get_today_calendar_events(self):
        return []

    async def get_courses(self):
        return [
            {"id": 1, "name": "Estadística Aplicada"},
            {"id": 2, "name": "Lenguajes de Programación II"},
            {"id": 3, "name": "Sistema de Gestión de Base de Datos"},
            {"id": 4, "name": "Diseño de Software"},
            {"id": 5, "name": "Diseño de Interfaces"},
        ]

    async def get_assignments_with_scores(self, course_id):
        if course_id == 2:
            return [
                {"title": "Evaluación continua 01", "score": 18.0, "points_possible": 20.0, "graded": True},
                {"title": "Tarea02", "score": None, "points_possible": 20.0, "graded": False},
            ]
        return []


@pytest.fixture(scope="module")
def reg():
    r = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(r)
    return r


@pytest.fixture
def canvas(reg, monkeypatch):
    sk = reg.get_skill("canvas")
    if sk is None:
        pytest.skip("canvas no cargó")
    monkeypatch.setattr(sk, "_get_client", lambda: _StubClient())
    sk._cache.clear()
    return sk


def _ask(canvas, text):
    return asyncio.run(canvas.handle(text, ctx=None))


# ── Normalización ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Diseño de Software", "diseno de software"),
    ("Estadística Aplicada", "estadistica aplicada"),
    ("  LENGUAJES  ", "lenguajes"),
    ("", ""),
])
def test_norm_strips_accents_and_case(raw, expected):
    assert _norm(raw) == expected


def test_significant_words_drops_stopwords_and_short_ones():
    assert _significant_words("Sistema de Gestión de Base de Datos") == {
        "sistema", "gestion", "base", "datos",
    }


# ── _names_overlap ───────────────────────────────────────────────────────────

def test_empty_course_name_matches_nothing(canvas):
    """El bug: `"" in b` daba True y la entrega huérfana caía en cualquier curso."""
    assert canvas._names_overlap("", "Diseño de Software") is False
    assert canvas._names_overlap("Diseño de Software", "") is False


def test_one_shared_word_is_not_enough(canvas):
    """"Base" sola cruzaba cursos que no tienen nada que ver."""
    assert canvas._names_overlap(
        "Sistema de Gestión de Base de Datos", "Base de Datos Avanzada II"
    ) is False


@pytest.mark.parametrize("a,b", [
    ("Diseño de Software", "diseno de software"),
    ("Lenguajes de Programación II", "Lenguajes de Programación II"),
    ("Programación II", "Lenguajes de Programación II"),
])
def test_same_course_still_matches(canvas, a, b):
    assert canvas._names_overlap(a, b) is True


# ── El caso del bug, end-to-end ──────────────────────────────────────────────

def test_course_without_grade_is_found_and_reported_as_such(canvas):
    out = _ask(canvas, "cómo voy en diseño de software")
    assert out is not None
    low = out.lower()
    assert "no encontré un curso" not in low, "el curso existe: no puede reportarse como inexistente"
    assert "nota" in low
    # Y jamás debe mezclarlo con otro curso
    assert "base de datos" not in low


def test_grades_listing_includes_the_ungraded_course(canvas):
    out = _ask(canvas, "cuáles son mis notas de canvas")
    assert out is not None
    assert "Diseño de Software" in out


def test_orphan_submission_is_not_attributed_to_a_course(canvas):
    out = _ask(canvas, "cómo voy en diseño de software")
    assert out is not None
    assert "Entrega sin curso" not in out


def test_real_missing_submission_still_shows_in_its_course(canvas):
    out = _ask(canvas, "cómo voy en lenguajes de programación ii")
    assert out is not None
    assert "Tarea02" in out


# ── Siglas: "EC1" es "Evaluación continua 01" ────────────────────────────────

from skills.builtin.canvas.skill import _expand_eval_alias, _score_eval  # noqa: E402


@pytest.mark.parametrize("sigla,esperado", [
    ("EC1", "evaluacion continua 1"),
    ("ec 2", "evaluacion continua 2"),
    ("ED", "ed"),                      # sin número no es sigla resoluble
    ("S1", "semana 1"),
    ("TP3", "trabajo practico 3"),
    ("evaluacion continua 1", "evaluacion continua 1"),
])
def test_expand_eval_alias(sigla, esperado):
    assert _expand_eval_alias(sigla) == esperado


def test_ec1_matches_the_real_title():
    assert _score_eval("EC1", "Evaluación continua 01") > 0.8


def test_ec1_does_not_match_a_different_number():
    """Lo peligroso no es no encontrarla: es devolver la nota de OTRA evaluación."""
    assert _score_eval("EC1", "Evaluación continua 02") == 0.0


def test_ec1_resolves_end_to_end(canvas):
    out = _ask(canvas, "cuál es mi nota en la EC1 de lenguajes de programación ii")
    assert out is not None
    assert "18" in out, out
    assert "no encontré" not in out.lower()


# ── Preguntar en vez de adivinar, y recordar la respuesta ────────────────────

def test_ambiguous_course_asks_instead_of_guessing(canvas):
    """Dos cursos empiezan con "Diseño": elegir uno en silencio es lo que estaba mal."""
    out = _ask(canvas, "cómo voy en diseño")
    assert out is not None
    low = out.lower()
    assert "no estoy seguro" in low and "¿es alguno de estos?" in low
    assert "Diseño de Software" in out and "Diseño de Interfaces" in out


def test_unambiguous_partial_name_still_answers_directly(canvas):
    """"gestión" aparece en un solo curso: preguntar ahí sería molestar al pedo."""
    out = _ask(canvas, "cómo voy en gestión")
    assert out is not None
    assert "no estoy seguro" not in out.lower()
    assert "Sistema de Gestión de Base de Datos" in out


def test_answering_the_question_resolves_and_is_remembered(canvas, monkeypatch):
    guardado = {}

    class _Prof:
        def get_preference(self, k, default=None):
            return guardado.get(k, default)

        def set_preference(self, k, v):
            guardado[k] = v

    monkeypatch.setattr(canvas, "_profile_cache", _Prof())

    primera = _ask(canvas, "cómo voy en diseño")
    assert "no estoy seguro" in primera.lower()

    # El usuario aclara
    respuesta = _ask(canvas, "Diseño de Software")
    assert respuesta is not None
    assert "Diseño de Software" in respuesta
    assert guardado, "el alias tendría que haber quedado guardado"

    # Y la próxima vez no vuelve a preguntar lo mismo
    canvas._pending = None
    segunda = _ask(canvas, "cómo voy en diseño")
    assert "no estoy seguro" not in segunda.lower()
    assert "Diseño de Software" in segunda


# ── Tabla comparativa ────────────────────────────────────────────────────────

def test_grades_render_as_a_markdown_table(canvas):
    out = _ask(canvas, "cuáles son mis notas de canvas")
    assert out is not None
    assert "| Curso | Nota |" in out and "|---|---|" in out
    assert "| Estadística Aplicada | 90.0" in out
    # El curso sin nota aparece, y dice por qué
    assert "| Diseño de Software | sin nota registrada |" in out


def test_grades_table_reports_the_average_of_graded_courses_only(canvas):
    out = _ask(canvas, "cuáles son mis notas de canvas")
    # (90 + 90 + 30 + 75) / 4 = 71.2 — el curso sin nota no promedia
    assert "71.2" in out, out
    assert "1 curso todavía sin nota" in out


def test_course_detail_lists_evaluations_and_flags_the_ungraded_ones(canvas):
    out = _ask(canvas, "cómo voy en lenguajes de programación ii")
    assert out is not None
    assert "| Evaluación | Nota |" in out
    assert "| Evaluación continua 01 | 18.0 / 20 |" in out
    assert "| Tarea02 | sin nota registrada |" in out


def test_a_clear_course_name_never_asks(canvas):
    out = _ask(canvas, "cómo voy en estadística aplicada")
    assert out is not None
    assert "no estoy seguro" not in out.lower()
    assert "Estadística Aplicada" in out


# ── Segunda ronda: la conversación real del 2026-09-30 ───────────────────────
# Bruno probó de nuevo y fallaron cuatro cosas más. Los nombres de curso de este
# stub son los REALES de su Canvas: la universidad trunca el título antes del
# pipe ("Sistema de Gestión de Base de | 5B TEO | 2274-5863 |"), así que el
# nombre completo que él escribe es MÁS LARGO que el que Canvas devuelve.

class _StubReal:
    CURSOS = [
        (92900, "Diseño de Software", None),
        (92751, "Estadística Aplicada", 90.0),
        (94926, "Lenguajes de Programación II", 90.0),
        (91499, "Sistema de Gestión de Base de", 30.0),
    ]

    def is_configured(self):
        return True

    async def get_course_grades(self):
        return [{"course": n, "course_id": i, "score": s, "grade": None} for i, n, s in self.CURSOS]

    async def get_courses(self):
        return [{"id": i, "name": f"{n} | 5B TEO | 2274 |"} for i, n, _ in self.CURSOS]

    async def get_missing_submissions(self):
        return []

    async def get_announcements(self, limit=10):
        return []

    async def get_upcoming_assignments(self, days=7):
        return [{"title": "Tarea03", "course": "Lenguajes de Programación II", "due_at": None}]

    async def get_today_calendar_events(self):
        return []

    async def get_assignments_with_scores(self, course_id):
        if course_id == 91499:
            return [{"title": "Evaluación continua 01", "score": 12.0,
                     "points_possible": 20.0, "graded": True}]
        if course_id == 92900:
            return [{"title": "Evaluación continua 01", "score": None,
                     "points_possible": 20.0, "graded": False}]
        return []


@pytest.fixture
def canvas_real(reg, monkeypatch):
    sk = reg.get_skill("canvas")
    if sk is None:
        pytest.skip("canvas no cargó")
    monkeypatch.setattr(sk, "_get_client", lambda: _StubReal())
    monkeypatch.setattr(sk, "_profile_cache", False)   # sin memoria de alias
    sk._cache.clear()
    sk._pending = None
    return sk


@pytest.mark.parametrize("pregunta", [
    "Cuanto tengo en la EC1 de gestion",
    "Cual es mi nota en la EC1 de sistema",
    "cuanto saque en la ec1 de sistema de gestion de base de datos",
    "qué nota tengo en la evaluacion continua 1 de gestion",
])
def test_specific_evaluation_is_claimed_and_answered(canvas_real, pregunta):
    """"Cuánto tengo en la EC1" no tiene la palabra "nota": el gate no la reclamaba
    y la consulta terminaba devolviendo las tareas próximas."""
    assert canvas_real.matches_command(pregunta) is True, "la skill tiene que reclamar el turno"
    out = _ask(canvas_real, pregunta)
    assert out is not None
    assert "12.0" in out, out
    assert "tarea03" not in out.lower(), "devolvió tareas próximas en vez de la nota"


def test_course_hint_ignores_the_evaluation_part(canvas_real):
    """"en la EC1 de sistema" dejaba el hint en "la ec1 de sistema" y no matcheaba."""
    from skills.builtin.canvas.skill import _COURSE_HINT_RE
    m = _COURSE_HINT_RE.search("cual es mi nota en la ec1 de sistema")
    hint = next((g for g in m.groups() if g), None)
    assert hint == "sistema"


def test_single_candidate_is_not_worth_a_question(canvas_real):
    """Preguntar "¿es este?" ofreciendo UNA sola opción es puro ruido."""
    out = _ask(canvas_real, "cómo voy en gestion")
    assert out is not None
    assert "no estoy seguro" not in out.lower()
    assert "Sistema de Gestión de Base de" in out


def test_an_ungraded_evaluation_says_so(canvas_real):
    out = _ask(canvas_real, "cuanto tengo en la ec1 de diseño")
    assert out is not None
    assert "no tiene nota cargada" in out.lower()


def test_plain_yes_accepts_the_only_offered_option(canvas_real, monkeypatch):
    """"si ese mismo" no se entendía y el turno se lo quedaba el LLM, que inventó
    que no había nota — cuando sí la había."""
    canvas_real._remember_question(
        "course", "sistema", ["Sistema de Gestión de Base de"],
        {"handler": "specific_eval", "eval_name": "ec1"},
    )
    assert canvas_real.matches_command("si ese mismo") is True
    out = _ask(canvas_real, "si ese mismo")
    assert out is not None and "12.0" in out, out


def test_yes_is_not_an_answer_when_several_options_were_offered(canvas_real):
    """Con varias opciones, un "sí" pelado no elige nada: hay que seguir preguntando."""
    canvas_real._remember_question(
        "course", "x", ["Diseño de Software", "Estadística Aplicada"],
        {"handler": "course_detail"},
    )
    assert canvas_real._match_answer("si") is None


# ── Tercera ronda: la misma evaluación en TODOS los cursos ───────────────────
# Bruno pidió "todas mis EC1" y el asistente devolvió las tareas próximas; tras
# tres intentos el turno se lo quedó el LLM, que respondió con una tabla ENTERA
# de notas inventadas (8,5 / 7,2 / 9,1 / 8,8 / 8,3 y un promedio falso) cuando la
# única EC1 calificada valía 12.0/20. El formato de tabla la hacía más creíble.

@pytest.mark.parametrize("pregunta", [
    "Muestrame todas mis EC1 de mis cursos en canvas",
    "Muestrame todas las notas de mi EC1 en canvas",
    "Muestrames todas las notas de mis cursos pero solo las EC1",
    "mostrame la evaluacion continua 1 de todos los cursos",
])
def test_same_evaluation_across_all_courses(canvas_real, pregunta):
    assert canvas_real.matches_command(pregunta) is True
    out = _ask(canvas_real, pregunta)
    assert out is not None
    assert "| Curso | Evaluación | Nota |" in out, out
    # El dato real, y solo el dato real
    assert "| Sistema de Gestión de Base de | Evaluación continua 01 | 12.0 / 20 |" in out
    assert "| Diseño de Software | Evaluación continua 01 | sin nota registrada |" in out
    # Un curso que no tiene esa evaluación se dice, no se rellena
    assert "no figura en este curso" in out
    assert "tarea03" not in out.lower(), "devolvió tareas próximas en vez de las notas"


def test_across_courses_summary_counts_the_three_states(canvas_real):
    out = _ask(canvas_real, "todas mis EC1")
    assert "1 con nota" in out
    assert "1 sin calificar todavía" in out
    assert "2 donde no figura" in out


def test_across_courses_average_only_counts_graded(canvas_real):
    out = _ask(canvas_real, "todas mis EC1")
    assert "60.0%" in out, out   # 12/20, y nada más


# ── No inventar: admitir el límite en vez de responder cualquier cosa ────────

def test_unrecognised_canvas_query_admits_it(canvas_real):
    """El default era "tareas próximas": cualquier consulta rara recibía una
    respuesta confiadamente equivocada."""
    out = _ask(canvas_real, "canvas hacé algo rarísimo que no existe")
    assert out is not None
    assert "no entendí qué querés de canvas" in out.lower()
    assert "tarea03" not in out.lower()


def test_clarification_in_an_open_canvas_conversation_is_claimed(canvas_real):
    """"no es un curso, me refiero a la EC1" lo contestaba el LLM, que inventó."""
    _ask(canvas_real, "todas mis EC1")          # abre la conversación
    assert canvas_real.matches_command(
        "no es un curso me refiero a la EC1 la evaluacion continua 1") is True


@pytest.mark.parametrize("frase", [
    "tengo un parcial el martes",
    "agendá el examen de cálculo para el martes",
    "recordame el parcial de mañana",
])
def test_an_open_conversation_does_not_steal_the_agenda(canvas_real, frase):
    """La ventana de follow-up exige forma de ACLARACIÓN, no solo mencionar una
    evaluación: si no, Canvas le robaba los turnos a schedule."""
    _ask(canvas_real, "todas mis EC1")
    assert canvas_real.matches_command(frase) is False


# ── Cuarta ronda: la palabra "canvas" no puede ser un agujero negro ──────────
# Bruno preguntó si la API permite descargar los PDF de cada módulo y recibió la
# lista de capacidades como si fuera la respuesta — dos veces. Diagnóstico suyo,
# y era exacto: matches_command devolvía True ante CUALQUIER mención de "canvas",
# así que toda pregunta sobre la plataforma caía en el dispatch de datos.

def test_api_question_gets_an_honest_answer_not_the_capability_list(canvas_real):
    out = _ask(canvas_real, "la api de canvas me permite descargar los pdf de cada modulo?")
    assert out is not None
    low = out.lower()
    assert "no entendí qué querés" not in low, "respondió con el cartel genérico"
    assert "no lo tengo implementado" in low
    assert "módulos" in low or "modulos" in low


def test_capability_question_is_answered_as_capabilities(canvas_real):
    out = _ask(canvas_real, "qué podés hacer con canvas")
    assert out is not None
    assert out.lower().startswith("hoy con canvas puedo")
    assert "no entendí" not in out.lower()


@pytest.mark.parametrize("frase", [
    "canvas es una buena plataforma?",
    "me gusta canvas",
    "odio canvas pero bueno",
])
def test_opinions_about_canvas_are_not_data_queries(canvas_real, frase):
    """Opinar sobre la plataforma no es pedir datos: que lo tome el chat."""
    assert canvas_real.matches_command(frase) is False


def test_an_opinion_with_a_real_request_still_counts(canvas_real):
    """La señal de datos gana sobre el marcador de opinión."""
    out = _ask(canvas_real, "no me gusta canvas, mostrame mis notas")
    assert out is not None
    assert "| Curso | Nota |" in out


@pytest.mark.parametrize("frase", [
    "qué tengo en canvas",
    "tengo pendientes en canvas",
    "Muestrame todas mis EC1",
])
def test_data_queries_are_untouched(canvas_real, frase):
    assert canvas_real.matches_command(frase) is True
    assert _ask(canvas_real, frase) is not None


# ── Quinta ronda: el título que ES la sigla ──────────────────────────────────
# Bruno mostró una captura: Ciberseguridad Global tiene EC1 = 17/20, y la tabla
# decía "no figura en este curso". No era la API ni cache vieja (la API devuelve
# title='EC1', score=17.0, graded=True): expandir "ec1" → "evaluacion continua 1"
# hacía que NO matcheara un título llamado literalmente "EC1", porque "ec1" tiene
# 3 letras y el filtro de palabras significativas lo descartaba. El bug escondía
# tres notas, no una.

@pytest.mark.parametrize("query,title", [
    ("ec1", "EC1"),
    ("ec1", "Evaluación continua 01"),
    ("ec1", "Evaluación Continua 1"),
    ("ec1", "EVALUACION CONTINUA 1 EC1 - SEMANA 5"),
    ("evaluacion continua 1", "EC1"),
    ("EC1", "ec1"),
])
def test_abbreviation_and_long_name_match_in_both_directions(query, title):
    assert _score_eval(query, title) > 0.8, f"{query!r} vs {title!r}"


@pytest.mark.parametrize("query,title", [
    ("ec1", "EC2"),
    ("ec1", "Evaluación continua 02"),
    ("ec2", "EC1"),
])
def test_the_number_still_decides(query, title):
    """Devolver la nota de OTRA evaluación es peor que no encontrarla."""
    assert _score_eval(query, title) == 0.0


def test_a_title_that_is_the_bare_abbreviation_is_found(canvas_real, monkeypatch):
    class _Siglas(_StubReal):
        async def get_assignments_with_scores(self, course_id):
            if course_id == 92900:      # Diseño de Software
                return [{"title": "EC1", "score": 17.0,
                         "points_possible": 20.0, "graded": True}]
            return []

    monkeypatch.setattr(canvas_real, "_get_client", lambda: _Siglas())
    canvas_real._cache.clear()
    out = _ask(canvas_real, "todas mis EC1")
    assert "| Diseño de Software | EC1 | 17.0 / 20 |" in out, out
    assert "no figura" not in out.split("Diseño de Software")[1][:40]


def test_no_average_when_the_scales_are_not_comparable(canvas_real, monkeypatch):
    """Una evaluación sin puntaje máximo no se puede promediar con las demás:
    se dice, no se omite en silencio."""
    class _SinEscala(_StubReal):
        async def get_assignments_with_scores(self, course_id):
            if course_id == 92900:
                return [{"title": "EC1", "score": 17.0, "points_possible": None, "graded": True}]
            if course_id == 91499:
                return [{"title": "EC1", "score": 12.0, "points_possible": 20.0, "graded": True}]
            return []

    monkeypatch.setattr(canvas_real, "_get_client", lambda: _SinEscala())
    canvas_real._cache.clear()
    out = _ask(canvas_real, "todas mis EC1")
    assert "no calculo promedio" in out.lower(), out
    assert "no son comparables" in out.lower()
