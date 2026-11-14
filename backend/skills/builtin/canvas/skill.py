"""Canvas LMS Skill — integración con Canvas API.

Intents soportados:
- upcoming    → tareas/entregas próximas (7 días)
- missing     → entregas faltantes o atrasadas
- grades      → calificaciones por curso
- announcements → anuncios recientes
- today       → eventos del calendario de hoy
"""
from __future__ import annotations

import time
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from core.app_config import AMBIGUITY_MARGIN, CLARIFY_HIGH, CLARIFY_LOW
from core.request_context import RequestContext
from skills.base import BaseSkill

import re

# ── Normalización de nombres ─────────────────────────────────────────────────
# Sin esto "diseño" y "diseno" son cadenas distintas, y el usuario escribe de las
# dos formas (y el STT de una tercera).

_STOPWORDS = frozenset({
    "de", "del", "la", "el", "los", "las", "y", "e", "en", "a", "al",
    "para", "con", "por", "un", "una",
})


def _norm(text: str) -> str:
    """Minúsculas y sin acentos: 'Diseño de Software' → 'diseno de software'."""
    decomposed = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").strip()


def _significant_words(name: str) -> set[str]:
    """Palabras que aportan identidad: sin stopwords y de más de 3 letras."""
    return {w for w in _norm(name).split() if len(w) > 3 and w not in _STOPWORDS}


# ── Siglas de evaluaciones ───────────────────────────────────────────────────
# El usuario dice "EC1" y en Canvas la evaluación se llama "Evaluación continua 01".
# Sin expandir la sigla, el matching por palabras da 0 y la skill respondía
# "no encontré una evaluación llamada 'ec1'" (bug real, 2026-09-30).
_EVAL_ALIASES: dict[str, str] = {
    "ec": "evaluacion continua",
    "ed": "evaluacion diagnostica",
    "ep": "examen parcial",
    "ef": "examen final",
    "tp": "trabajo practico",
    "ex": "examen",
    "s": "semana",
}
_RE_SIGLA = re.compile(r"^\s*([a-z]{1,2})\s*[-_. ]?\s*(\d+)\s*$")


def _expand_eval_alias(name: str) -> str:
    """'EC1' → 'evaluacion continua 1'. Si no es una sigla conocida, normaliza."""
    n = _norm(name)
    m = _RE_SIGLA.match(n)
    if m and m.group(1) in _EVAL_ALIASES:
        return f"{_EVAL_ALIASES[m.group(1)]} {int(m.group(2))}"
    return n


def _ordinal(name: str) -> Optional[int]:
    """Primer número del nombre, sin ceros a la izquierda: '01' y '1' son lo mismo."""
    m = re.search(r"\d+", _norm(name))
    return int(m.group()) if m else None


def _score_name(query: str, name: str) -> float:
    """Parecido 0..1 entre lo que dijo el usuario y un nombre real de Canvas."""
    q, n = _norm(query), _norm(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q in n or n in q:
        return 0.9
    qw, nw = _significant_words(q), _significant_words(n)
    if not qw or not nw:
        return 0.0
    return len(qw & nw) / len(qw | nw)


def _score_eval(query: str, title: str) -> float:
    """Como _score_name, pero el número manda: la EC1 no es la EC2.

    Sin la regla del número, "evaluacion continua 1" y "Evaluación continua 02"
    empatan en 1.0, porque el filtro de palabras significativas descarta los
    dígitos sueltos.

    Se comparan las CUATRO combinaciones de sigla y nombre largo. Expandir solo
    la consulta no alcanzaba: con un título que es literalmente "EC1", la query
    expandida ("evaluacion continua 1") daba 0.00 — "ec1" tiene 3 letras y el
    filtro de palabras significativas lo descartaba, dejando el denominador
    vacío. Esa era la razón de que la EC1 de Ciberseguridad (17/20) apareciera
    como "no figura en este curso" (bug real, 2026-10-01).
    """
    q_raw, t_raw = _norm(query), _norm(title)
    q_exp, t_exp = _expand_eval_alias(query), _expand_eval_alias(title)
    base = max(
        _score_name(q_raw, t_raw),
        _score_name(q_exp, t_raw),
        _score_name(q_raw, t_exp),
        _score_name(q_exp, t_exp),
    )
    qn = _ordinal(q_exp) if _ordinal(q_exp) is not None else _ordinal(q_raw)
    tn = _ordinal(t_exp) if _ordinal(t_exp) is not None else _ordinal(t_raw)
    if qn is not None and tn is not None and qn != tn:
        return 0.0
    return base


# Token de evaluación: sirve para reconocer que la consulta habla de una
# evaluación puntual aunque no siga ninguno de los patrones con nombre de curso.
_EVAL_TOKEN = (
    r'(?:ec\s*\d+|s\d+|parcial(?:es)?\s*\d*|examen(?:es)?\s*\d*'
    r'|evaluaci[oó]n(?:es)?\s*\w*\s*\d*|quiz(?:zes)?\s*\d*|tp\s*\d*)'
)
_EVAL_TOKEN_RE = re.compile(rf'\b{_EVAL_TOKEN}', re.IGNORECASE)

# "todas mis EC1", "la EC1 de todos mis cursos", "el parcial de cada materia".
# Junto con un token de evaluación, indica consulta transversal a los cursos.
_ACROSS_COURSES_RE = re.compile(r'\b(?:todas?|todos?|cada)\b', re.IGNORECASE)

# ── Preguntas que NO son consultas de datos ──────────────────────────────────
# Mencionar "canvas" reclamaba el turno SIEMPRE, así que cualquier pregunta sobre
# la plataforma —o sobre lo que el asistente sabe hacer— recibía la lista de
# capacidades como si fuera una respuesta. Parecía encasillado (y lo estaba).

# "qué podés hacer con canvas", "tus habilidades con canvas"
_RE_CAPABILITY_Q = re.compile(
    r'\b(?:qu[eé]\s+(?:pod[eé]s|puedes|sab[eé]s|sabes|cosas|funciones)'
    r'|tus\s+(?:habilidades|funciones|capacidades)'
    r'|para\s+qu[eé]\s+(?:sirve|serv[ií]s))',
    re.IGNORECASE,
)

# "¿la API permite…?", "¿se puede descargar…?", "¿soporta…?"
_RE_PLATFORM_Q = re.compile(
    r'\bapi\b|\bse\s+pued[eo]n?\b|\bes\s+posible\b|\bsoporta[sn]?\b'
    r'|\bpermit[ei][sn]?\b|\bhabr[ií]a\s+forma\b',
    re.IGNORECASE,
)

# Cosas que la API de Canvas expone y esta skill todavía NO implementa.
# Verificado contra el Canvas real (2026-09-30): modules e items responden OK y
# cada File trae url de descarga; el índice `courses/{id}/files` da 403 en esa
# institución, así que el camino viable es módulos → items → files/{id}.
_RE_NOT_IMPLEMENTED = re.compile(
    r'\b(?:archivos?|pdf[s]?|material(?:es)?|apuntes?|m[oó]dulos?|diapositivas?|'
    r'ppt[sx]?|descarg\w+|subir|entregar|foros?|discusiones?|s[ií]labo|syllabus)\b',
    re.IGNORECASE,
)

# Opinión o comentario sobre la plataforma: no es una consulta, es charla.
_RE_OPINION = re.compile(
    r'\b(?:me\s+gusta|no\s+me\s+gusta|odio|detesto|es\s+(?:una\s+)?(?:buena?|mala?|'
    r'genial|horrible|lenta?)|qu[eé]\s+(?:opin[aá]s|opinas|te\s+parece)|te\s+parece)\b',
    re.IGNORECASE,
)

_CAPACIDADES = (
    "Hoy con Canvas puedo: las notas de todos tus cursos, la nota de una "
    "evaluación puntual («cuánto tengo en la EC1 de redes»), la misma evaluación "
    "en todos los cursos («todas mis EC1»), las entregas faltantes, las tareas "
    "próximas y los anuncios."
)

_NO_IMPLEMENTADO = (
    "Eso todavía no lo tengo implementado. La API de Canvas sí lo expone —los "
    "módulos de cada curso listan sus archivos y cada uno trae URL de descarga—, "
    "pero yo todavía no leo ni descargo material: solo consulto notas, entregas, "
    "tareas y anuncios.\n\n" + _CAPACIDADES
)


# Forma de aclaración: el usuario corrige lo que entendimos mal.
_RE_ACLARACION = re.compile(
    r'^\s*(?:no\b|me\s+refiero|quise\s+decir|te\s+digo|hablo\s+de|es\s+(?:la|el)\b'
    r'|estoy\s+hablando\s+de)',
    re.IGNORECASE,
)

# Afirmaciones sueltas con las que el usuario acepta la única opción ofrecida.
_RE_AFIRMACION = re.compile(
    r'^\s*(?:s[ií]|sip|dale|correcto|exacto|claro|obvio|ese|esa|ese\s+mismo|esa\s+misma|'
    r'el\s+mismo|la\s+misma|ese\s+es|esa\s+es|justo|aja|ok|okey)\b',
    re.IGNORECASE,
)


def _rank(query: str, items: list, key, scorer=_score_name) -> list[tuple[float, dict]]:
    """Candidatos con score > 0, del más parecido al menos."""
    scored = [(scorer(query, key(it)), it) for it in items]
    return sorted([p for p in scored if p[0] > 0], key=lambda p: p[0], reverse=True)


# Palabras clave para cada intent
_KW_MISSING     = ("sin entregar", "faltante", "faltantes", "atrasado", "atrasada", "missing")
_KW_GRADES      = ("calificaciones", "calificacion", "calificación", "notas", "nota", "promedio", "promedios", "grades")
_KW_ANNOUNCE    = ("anuncios", "anuncio", "actividad reciente", "novedad", "novedades")
_KW_TODAY       = ("hoy", "calendario", "eventos hoy", "agenda canvas", "today")
_KW_UPCOMING    = ("tareas", "pendientes", "próximas", "proximas", "entregas", "upcoming", "assignments")

# "nota de la EC1 en redes", "mi nota en el parcial 1", "calificación de la S1"
_SPECIFIC_EVAL_RE = re.compile(
    # "cuánto tengo en la EC1" es la forma más natural de preguntarlo y no estaba:
    # sin "nota" la consulta caía al default y devolvía las tareas próximas.
    r'(?:'
    r'(?:nota|calificaci[oó]n|puntaje)\s+(?:de|en)'
    r'|cu[aá]nto\s*(?:tengo|saqu[eé]|obtuve|me\s+fue|me\s+saqu[eé])?\s*(?:de|en)'
    r'|qu[eé]\s+(?:nota|puntaje)\s+(?:tengo\s+|saqu[eé]\s+)?(?:de|en)'
    r')\s+(?:la\s+|el\s+)?'
    r'(ec\s*\d+|s\d+|parcial\s*\d*|examen\s*\d*|evaluaci[oó]n\s*\w*\s*\d*|quiz\s*\d*)',
    re.IGNORECASE,
)

# Patrones para consulta de curso específico:
# "cómo voy en sistemas operativos", "detalles de cálculo", "qué tengo en diseño de bases de datos"
# NOTA: Se eliminaron "resumen de", "información de", "cuéntame de" — son demasiado genéricos
# y causan falsos positivos en queries de noticias/web. Solo se mantienen patrones Canvas-específicos.
_COURSE_DETAIL_RE = re.compile(
    r'(?:c[oó]mo\s+(?:voy|estoy|ando)\s+en|detalles?\s+(?:de|del)|'
    r'qu[eé]\s+tengo\s+en)\s+(.+)',
    re.IGNORECASE,
)

# "cual es mi nota en redes", "cual es mi nota en la EC1 de redes", "qué nota tengo en X"
# Requiere "de/en + contenido" para evitar match de "mis notas" suelto sin contexto de curso.
_GRADE_QUERY_RE = re.compile(
    # El determinante es obligatorio: sin él, "tomá nota de esto" caería acá.
    # Antes solo se aceptaba el posesivo ("mi nota"), así que el follow-up natural
    # "y cuál es la nota en diseño de software" no lo atendía la skill — se iba al
    # LLM de chat, que tenía las notas del turno anterior en el historial e inventó
    # una relación entre dos cursos (bug real, 2026-09-30).
    r'(?:cu[aá]l(?:es)?\s+(?:es|son)\s+)?(?:(?:mi[s]?|la|el|las|los)\s+notas?'
    r'|qu[eé]\s+nota[s]?\s+tengo)'
    r'(?:'
    r'(?:\s+(?:de|en)\s+(?:la\s+)?(?:ec\d+|parcial[es]?|examen[^a-z]*\d*|evaluaci[oó]n\s*\w*)\s+(?:de\s+)?)'
    r'|(?:\s+(?:de|en|del?)\s+\w.+)'
    r')',
    re.IGNORECASE,
)

# STT near-misses: "campo" suena como "canvas" en español rápido (KAN-vas → KAM-po).
# Solo activa Canvas si el resto de la frase también tiene keywords de tareas/notas,
# para evitar falsos positivos con "campo" usado en otro contexto.
_STT_CANVAS_RE = re.compile(
    r'(?:'
    r'\bcampo\b(?=.*\b(?:tareas?|pendientes?|entregas?|notas?|calificaci[oó]n[es]*|asignaci[oó]n[es]*|canvas)\b)'
    r'|(?:\b(?:tareas?|pendientes?|entregas?|notas?|calificaci[oó]n[es]*)\b).*\bcampo\b'
    r')',
    re.IGNORECASE | re.DOTALL,
)

# Palabras que indican contexto web/noticias — si aparecen, la skill Canvas no debe activarse.
_WEB_CONTEXT_BLOCKLIST = frozenset({
    "noticias", "noticia", "internet", "en la web", "en google",
    "en la red", "investiga", "investigar", "busca en", "online",
    "en línea", "en linea", "actualidad", "últimas noticias", "ultimas noticias",
})

# Contexto de AGENDA/horario: si aparece SIN señal de dominio Canvas, la query es de
# schedule, no de Canvas. Evita que "qué tengo en la semana/agenda" la robe Canvas por
# _COURSE_DETAIL_RE ("qué tengo en X" tomaría "la semana" como nombre de curso).
_AGENDA_CONTEXT_RE = re.compile(
    r'\bagenda\b|\bcalendario\b|\bhorarios?\b'
    r'|(?:esta|la|pr[oó]xima)\s+semana'
    r'|\bqu[eé]\s+tengo\s+(?:hoy|ma[ñn]ana|esta\s+(?:semana|noche|tarde)'
    r'|el\s+(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo))\b',
    re.IGNORECASE,
)
# Señal de dominio Canvas (gana sobre el contexto de agenda si ambas aparecen).
_CANVAS_DOMAIN_RE = re.compile(
    r'\b(?:canvas|tareas?|pendientes?|entregas?|notas?|calificaci[oó]n(?:es)?|assignments?)\b',
    re.IGNORECASE,
)

# "hay pendientes en X", "tengo pendientes de X", "pendientes de redes".
# Tolera insertos comunes del habla ("pendientes O EXÁMENES en", "pendientes
# ESTA SEMANA en") de forma acotada — no un gap genérico, que robaría queries
# de agenda. El lookahead negativo evita capturar targets de agenda/calendario
# ("pendientes esta semana en mi agenda" es de schedule, no de Canvas).
_PENDING_QUERY_RE = re.compile(
    r'(?:hay|tengo|cu[aá]ntos?)\s+(?:tareas?\s+)?pendientes?'
    r'(?:\s+(?:o\s+\w+|esta\s+semana|para\s+(?:hoy|ma[ñn]ana)))?'
    r'\s+(?:en|de|para)\s+(?!(?:mi|la|el)\s+(?:agenda|calendario|semana)\b)'
    r'|\bpendientes?(?:\s+(?:o\s+\w+|esta\s+semana))?'
    r'\s+(?:de|en|para)\s+(?!(?:mi|la|el)\s+(?:agenda|calendario|semana)\b)\w'
    r'|\ben\s+\w.{2,50}\s+hay\s+pendientes?',
    re.IGNORECASE,
)

# Extrae el nombre de curso de queries naturales: "nota en la EC1 de X" → "X"
_COURSE_HINT_RE = re.compile(
    # La primera alternativa recorta la evaluación para quedarse solo con el curso.
    # Antes exigía "DE la EC1 de X" y no cubría "EN la EC1 de X", así que el hint
    # quedaba "la ec1 de sistema" y el curso no matcheaba con nada.
    r'\b(?:de|en)\s+(?:la\s+|el\s+)?'
    r'(?:ec\s*\d+|s\d+|parcial[es]?\s*\d*|examen[^a-z]*\d*|evaluaci[oó]n\s*\w*\s*\d*|quiz\s*\d*)'
    r'\s+(?:de|en)\s+(.+)$'
    r'|\b(?:en|de)\s+(.{3,})$',
    re.IGNORECASE,
)

# Tokens que nunca son un nombre de curso (la plataforma misma, palabras de
# dominio y contexto de agenda).
_COURSE_DISCARD = frozenset({
    "canvas", "mis notas", "notas", "calificaciones",
    "mi agenda", "la agenda", "agenda", "el calendario", "la semana", "",
})


def _clean_course_candidate(raw: str) -> str:
    """Normaliza un candidato a nombre de curso extraído del habla.

    El STT deja puntuación colgando ('canvas.', 'redes?') que rompía tanto el
    discard por igualdad exacta como el substring-match contra los nombres
    reales de curso. Devuelve "" si el candidato no es un curso.
    """
    candidate = (raw or "").strip().strip(" .?!,;:¿¡").strip()
    if candidate.lower() in _COURSE_DISCARD:
        return ""
    return candidate

_DAYS_LABELS = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
    6: "domingo",
}


def _relative_date(dt: datetime) -> str:
    """Return a human-friendly relative date string in Spanish.

    Converts to local timezone before computing day labels so that a deadline
    like Sunday 23:59 (Peru, UTC-5) isn't shown as "lunes" because Canvas
    stores it as Monday 04:59 UTC.
    """
    local_dt = dt.astimezone()
    now_local = datetime.now(tz=timezone.utc).astimezone()
    delta = (local_dt.date() - now_local.date()).days
    if delta == 0:
        return "hoy"
    if delta == 1:
        return "mañana"
    if 2 <= delta <= 6:
        return f"el {_DAYS_LABELS.get(local_dt.weekday(), '')}"
    return local_dt.strftime("%d/%m")


class CanvasSkill(BaseSkill):

    CACHE_TTL = 600  # 10 minutos

    # Ventana para contestar una desambiguación. Pasada, la pregunta caduca y el
    # mensaje del usuario vuelve a tratarse como una consulta nueva.
    PENDING_TTL = 180.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client = None
        self._cache: dict[str, tuple[object, float]] = {}
        self._pending: Optional[dict] = None
        self._profile_cache = None
        self._last_answer_ts: float = 0.0

    def _get_client(self):
        if self._client is None:
            from modules.canvas.client import CanvasClient
            self._client = CanvasClient()
        return self._client

    # ── Alias aprendidos ──────────────────────────────────────────
    # Cuando el usuario aclara a qué se refería, se guarda para no volver a
    # preguntar lo mismo. Se reusa UserProfile, que ya es el almacén de
    # preferencias del asistente.

    def _profile(self):
        if self._profile_cache is None:
            try:
                from modules.memory_extended.user_profile import UserProfile
                self._profile_cache = UserProfile()
            except Exception:
                self._profile_cache = False   # no reintentar en cada turno
        return self._profile_cache or None

    def _learned_alias(self, kind: str, query: str) -> Optional[str]:
        prof = self._profile()
        if prof is None or not query:
            return None
        try:
            return prof.get_preference(f"canvas_alias_{kind}_{_norm(query)}")
        except Exception:
            return None

    def _learn_alias(self, kind: str, query: str, real_name: str) -> None:
        prof = self._profile()
        if prof is None or not query or not real_name:
            return
        try:
            prof.set_preference(f"canvas_alias_{kind}_{_norm(query)}", real_name)
        except Exception:
            pass

    # ── Resolver un nombre: responder, preguntar, o admitir que no está ───

    def _resolve(self, kind: str, query: str, items: list, key, scorer=_score_name):
        """→ (elegido, None) si está claro · (None, opciones) si hay que preguntar
        · (None, []) si no hay ningún candidato.

        Antes se devolvía el mejor candidato aunque compartiera UNA sola palabra,
        en silencio y sin margen de duda. De ahí salían las respuestas sobre el
        curso equivocado.
        """
        learned = self._learned_alias(kind, query)
        if learned:
            for it in items:
                if _norm(key(it)) == _norm(learned):
                    return it, None

        ranked = _rank(query, items, key, scorer)
        if not ranked:
            return None, []

        top_score, top = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        if top_score >= CLARIFY_HIGH and (top_score - runner_up) > AMBIGUITY_MARGIN:
            return top, None
        # Con UN solo candidato plausible no hay nada que desambiguar: preguntar
        # "¿es este?" ofreciendo una única opción es puro ruido.
        if len(ranked) == 1 and top_score >= CLARIFY_LOW:
            return top, None
        return None, [key(it) for _s, it in ranked[:4]]

    # ── Ida y vuelta de la desambiguación ────────────────────────────────

    def _remember_question(self, kind: str, query: str, options: list, retry: dict) -> None:
        """Guarda qué se preguntó para poder entender la respuesta del próximo turno."""
        self._pending = {
            "kind": kind, "query": query, "options": list(options),
            "retry": dict(retry), "ts": time.time(),
        }

    def _pending_question(self) -> Optional[dict]:
        if not self._pending:
            return None
        if time.time() - self._pending.get("ts", 0) > self.PENDING_TTL:
            self._pending = None
            return None
        return self._pending

    def _match_answer(self, reply: str) -> Optional[str]:
        """¿La respuesta del usuario elige una de las opciones que le ofrecimos?"""
        pend = self._pending_question()
        if not pend:
            return None
        options = pend["options"]
        # "sí", "ese mismo", "dale": afirmar solo es respuesta si ofrecimos UNA opción.
        if len(options) == 1 and _RE_AFIRMACION.search(_norm(reply)):
            return options[0]
        # "el primero" / "la 2"
        m = re.search(r"\b(\d+)\b", _norm(reply))
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(options) and len(_norm(reply).split()) <= 4:
                return options[idx]
        ranked = _rank(reply, options, lambda o: o)
        if ranked and ranked[0][0] >= CLARIFY_HIGH:
            return ranked[0][1]
        return None

    @staticmethod
    def _has_data_signal(lower: str) -> bool:
        """¿La frase pide datos concretos, o solo menciona Canvas de pasada?"""
        if _EVAL_TOKEN_RE.search(lower):
            return True
        return any(
            k in lower
            for grupo in (_KW_MISSING, _KW_GRADES, _KW_ANNOUNCE, _KW_TODAY, _KW_UPCOMING)
            for k in grupo
        )

    @staticmethod
    def _ask(query: str, options: list, noun: str) -> str:
        opciones = "\n".join(f"- {o}" for o in options)
        return (
            f"No estoy seguro de a qué {noun} te referís con «{query}». "
            f"¿Es alguno de estos?\n{opciones}"
        )

    # ── Caché ─────────────────────────────────────────────────────

    async def _cached(self, key: str, factory) -> object:
        """Return cached value if fresh, otherwise call factory() and cache result.

        factory must be a callable (e.g. lambda) that returns a coroutine,
        so the coroutine is only created when the cache misses.
        """
        entry = self._cache.get(key)
        if entry and time.monotonic() - entry[1] < self.CACHE_TTL:
            return entry[0]
        result = await factory()
        self._cache[key] = (result, time.monotonic())
        return result

    # ── Matching ──────────────────────────────────────────────────

    def matches_command(self, user_input: str) -> bool:
        lower = user_input.lower().strip()
        if any(w in lower for w in _WEB_CONTEXT_BLOCKLIST):
            return False
        # Si acabamos de preguntar cuál curso/evaluación era, la respuesta es
        # nuestra aunque no mencione Canvas ("el segundo", "diseño de software").
        if self._match_answer(user_input):
            return True
        if re.search(r'\bcanvas\b', lower):
            # Mencionar Canvas no convierte cualquier frase en una consulta de
            # datos. Se reclama la pregunta por capacidades (es nuestra), y la
            # pregunta por la plataforma SOLO si es sobre algo que podríamos
            # hacer y no hacemos — ahí la respuesta honesta la tenemos nosotros.
            # Lo demás ("canvas es buena plataforma", "me gusta canvas") es chat.
            if _RE_CAPABILITY_Q.search(lower):
                return True
            if _RE_PLATFORM_Q.search(lower):
                return bool(_RE_NOT_IMPLEMENTED.search(lower))
            if _RE_OPINION.search(lower) and not self._has_data_signal(lower):
                return False
            return True
        # Query de AGENDA (semana/agenda/calendario/"qué tengo hoy") sin señal de dominio
        # Canvas → es de schedule; no la claimes (evita "No encontré un curso 'la semana'").
        if _AGENDA_CONTEXT_RE.search(lower) and not _CANVAS_DOMAIN_RE.search(lower):
            return False
        if _STT_CANVAS_RE.search(lower):
            return True
        for trig in self.metadata.triggers:
            if lower == trig.lower() or lower.startswith(trig.lower()):
                return True
        if _COURSE_DETAIL_RE.search(lower):
            return True
        # El gate no chequeaba las consultas por evaluación puntual, así que
        # "cuánto tengo en la EC1 de X" ni llegaba al dispatch que sí sabe
        # resolverlas: la skill no reclamaba el turno y contestaba el LLM.
        if _SPECIFIC_EVAL_RE.search(lower):
            return True
        if _GRADE_QUERY_RE.search(lower):
            return True
        if _PENDING_QUERY_RE.search(lower):
            return True
        # Habla de una evaluación Y de notas: es nuestro dominio aunque la frase
        # no siga ninguna plantilla. Si no lo reclamamos, el turno se lo lleva el
        # LLM de chat, que tiene las notas del turno anterior en el historial y
        # completa lo que le falta inventando (pasó dos veces, 2026-09-30).
        if _EVAL_TOKEN_RE.search(lower) and any(k in lower for k in _KW_GRADES):
            return True
        # "todas mis EC1", "el parcial de cada curso": intención transversal clara.
        # El guard de agenda ya corrió arriba, así que "todos los exámenes de la
        # semana" sigue siendo de schedule.
        if _EVAL_TOKEN_RE.search(lower) and _ACROSS_COURSES_RE.search(lower):
            return True
        # Aclaración dentro de una conversación de Canvas ya abierta ("no, me
        # refiero a la EC1"). Sin esto la contesta el LLM, que inventa. Se exige
        # la FORMA de aclaración además del token: con solo el token, un
        # "tengo un parcial el martes" le robaría el turno a la agenda.
        if (_EVAL_TOKEN_RE.search(lower)
                and _RE_ACLARACION.search(lower)
                and time.time() - self._last_answer_ts < self.PENDING_TTL):
            return True
        return False

    @staticmethod
    def _extract_course_hint(lower: str) -> str:
        """Extract course name from a natural-language query."""
        m = _COURSE_HINT_RE.search(lower.strip())
        if not m:
            return ""
        # group(1) = "de EC1 de X" pattern; group(2) = generic "en/de X"
        return _clean_course_candidate(m.group(1) or m.group(2) or "")

    # ── Main handler ──────────────────────────────────────────────

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None
        result = await self._dispatch(user_input)
        if result:
            # Marca de conversación abierta sobre Canvas: los follow-ups cortos
            # de los próximos minutos también son nuestros (ver matches_command).
            self._last_answer_ts = time.time()
        return result

    async def handle_forced(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        """El clasificador cloud forzó esta skill justamente porque
        matches_command() no la reconoció — saltar el guard y despachar
        directo por keywords de intent (mismo patrón que ScheduleSkill)."""
        return await self._dispatch(user_input)

    async def _dispatch(self, user_input: str) -> Optional[str]:
        client = self._get_client()
        if not client.is_configured():
            return (
                "Canvas no está configurado. "
                "Añadí CANVAS_BASE_URL y CANVAS_API_TOKEN en backend/.env "
                "y reiniciá el servidor."
            )

        lower = user_input.lower()

        # ¿Es la respuesta a una desambiguación que hicimos recién?
        chosen = self._match_answer(user_input)
        if chosen:
            pend = self._pending or {}
            self._pending = None
            # Lo aprendido evita repetir la misma pregunta la próxima vez.
            self._learn_alias(pend.get("kind", ""), pend.get("query", ""), chosen)
            retry = pend.get("retry", {})
            try:
                if retry.get("handler") == "specific_eval":
                    if pend.get("kind") == "course":
                        return await self._handle_specific_evaluation(
                            client, retry.get("eval_name", ""), chosen)
                    return await self._handle_specific_evaluation(
                        client, chosen, retry.get("course_hint", ""))
                return await self._handle_course_detail(client, chosen)
            except Exception as exc:
                return f"No pude consultar Canvas: {exc}"

        # Preguntas sobre qué sé hacer / qué se puede hacer: se contestan sin
        # pegarle a la API, y ANTES que cualquier rama de datos.
        if _RE_NOT_IMPLEMENTED.search(lower) and _RE_PLATFORM_Q.search(lower):
            return _NO_IMPLEMENTADO
        if _RE_CAPABILITY_Q.search(lower):
            return _CAPACIDADES

        try:
            # "todas mis EC1", "la EC1 de todos mis cursos" → una fila por curso.
            # Va PRIMERO: si no, "todas las notas de mi EC1" cae en la rama de
            # curso puntual y busca un curso llamado "mi ec1 en canvas".
            m_eval = _EVAL_TOKEN_RE.search(lower)
            if m_eval and _ACROSS_COURSES_RE.search(lower):
                return await self._handle_evaluation_across_courses(
                    client, m_eval.group(0).strip())

            # Evaluación específica: "nota de la EC1 en redes", "nota de la S1 de SO"
            m = _SPECIFIC_EVAL_RE.search(lower)
            if m:
                eval_name = m.group(1).strip()
                hint = self._extract_course_hint(lower)
                return await self._handle_specific_evaluation(client, eval_name, hint)

            # Consulta de curso específico tiene prioridad
            m = _COURSE_DETAIL_RE.search(lower)
            if m:
                course_query = _clean_course_candidate(m.group(1))
                if not course_query:
                    # "qué tengo en canvas" (bareword, sin curso real) → resumen general.
                    return await self._handle_upcoming(client)
                return await self._handle_course_detail(client, course_query)

            # "cual es mi nota en X" / "qué nota tengo en X"
            if _GRADE_QUERY_RE.search(lower):
                hint = self._extract_course_hint(lower)
                if hint:
                    return await self._handle_course_detail(client, hint)
                return await self._handle_grades(client)

            # "hay pendientes en X" / "tengo pendientes de X"
            if _PENDING_QUERY_RE.search(lower):
                hint = self._extract_course_hint(lower)
                if hint:
                    return await self._handle_course_detail(client, hint)
                return await self._handle_missing(client)

            if any(kw in lower for kw in _KW_MISSING):
                return await self._handle_missing(client)
            if any(kw in lower for kw in _KW_GRADES):
                return await self._handle_grades(client)
            if any(kw in lower for kw in _KW_ANNOUNCE):
                return await self._handle_announcements(client)
            if any(kw in lower for kw in _KW_TODAY):
                return await self._handle_today(client)
            if any(kw in lower for kw in _KW_UPCOMING):
                return await self._handle_upcoming(client)

            # Antes se caía a las tareas próximas por defecto: cualquier consulta
            # que no matcheara un patrón recibía una respuesta confiadamente
            # equivocada ("cuánto tengo en la EC1" → lista de entregas). Admitir el
            # límite es parte del trabajo, y además evita que el turno se lo quede
            # el LLM, que rellena el hueco con datos inventados.
            return f"No entendí qué querés de Canvas. {_CAPACIDADES}"
        except Exception as exc:
            return f"Error al consultar Canvas: {exc}"

    # ── Intent handlers ───────────────────────────────────────────

    async def _handle_upcoming(self, client) -> str:
        items = await self._cached("upcoming", lambda: client.get_upcoming_assignments(days=7))
        if not items:
            return "No tenés tareas ni entregas en los próximos 7 días en Canvas."
        lines = [f"Tenés {len(items)} entrega{'s' if len(items) != 1 else ''} próxima{'s' if len(items) != 1 else ''} en Canvas."]
        for item in items:
            due = item.get("due_at")
            course = f" ({item['course']})" if item.get("course") else ""
            if due is None:
                # El cliente filtra las sin fecha, pero un None acá tiraba
                # AttributeError y la respuesta entera se volvía "Error al
                # consultar Canvas".
                lines.append(f"Sin fecha: {item['title']}{course}.")
                continue
            rel = _relative_date(due)
            time_str = due.astimezone().strftime("%H:%M")
            lines.append(f"{rel.capitalize()} a las {time_str}: {item['title']}{course}.")
        return " ".join(lines)

    async def _handle_missing(self, client) -> str:
        items = await self._cached("missing", lambda: client.get_missing_submissions())
        if not items:
            return "No tenés entregas faltantes ni atrasadas en Canvas."
        lines = [f"Tenés {len(items)} entrega{'s' if len(items) != 1 else ''} faltante{'s' if len(items) != 1 else ''} en Canvas."]
        for item in items:
            due_str = ""
            if item.get("due_at"):
                due_str = f" (venció el {item['due_at'].astimezone().strftime('%d/%m')})"
            pts = f", {item['points_possible']} pts" if item.get("points_possible") else ""
            lines.append(f"{item['title']}{due_str}{pts}.")
        return " ".join(lines)

    async def _handle_grades(self, client) -> str:
        grades = await self._cached("grades", lambda: client.get_course_grades())
        if not grades:
            return "No encontré calificaciones en Canvas. Puede que aún no haya notas cargadas."

        # Tabla Markdown: la UI la renderiza como tabla real (src/rich-render.js) y
        # el output de una skill no pasa por el sanitizer, así que llega intacta.
        # En audio no molesta: prepare_for_tts() ya descarta las filas de tabla.
        # En Telegram se ve con pipes literales — legible, aunque sin alinear.
        con_nota = [g for g in grades if g.get("score") is not None]
        filas = ["| Curso | Nota |", "|---|---|"]
        for g in sorted(grades, key=lambda g: (g.get("score") is None, -(g.get("score") or 0))):
            if g.get("score") is None:
                filas.append(f"| {g['course']} | sin nota registrada |")
            else:
                letra = f" ({g['grade']})" if g.get("grade") else ""
                filas.append(f"| {g['course']} | {float(g['score']):.1f}{letra} |")

        resumen = f"Tus calificaciones en Canvas ({len(grades)} cursos):"
        tabla = "\n".join(filas)
        if con_nota:
            prom = sum(float(g["score"]) for g in con_nota) / len(con_nota)
            cierre = f"\nPromedio de los {len(con_nota)} cursos con nota: {prom:.1f}."
        else:
            cierre = "\nTodavía no hay ninguna nota cargada."
        faltan = len(grades) - len(con_nota)
        if faltan:
            cierre += (
                f" {faltan} curso todavía sin nota." if faltan == 1
                else f" {faltan} cursos todavía sin nota."
            )
        return f"{resumen}\n\n{tabla}\n{cierre}"

    async def _handle_announcements(self, client) -> str:
        items = await self._cached("announcements", lambda: client.get_announcements(limit=5))
        if not items:
            return "No hay anuncios recientes en tus cursos de Canvas."
        lines = [f"Hay {len(items)} anuncio{'s' if len(items) != 1 else ''} reciente{'s' if len(items) != 1 else ''} en Canvas."]
        for item in items:
            course = f" en {item['course']}" if item.get("course") else ""
            msg = f" — {item['message']}" if item.get("message") else ""
            lines.append(f"{item['title']}{course}.{msg}")
        return " ".join(lines)

    async def _handle_today(self, client) -> str:
        items = await self._cached("today", lambda: client.get_today_calendar_events())
        if not items:
            return "No tenés eventos de Canvas en el calendario de hoy."
        lines = [f"Hoy tenés {len(items)} evento{'s' if len(items) != 1 else ''} en Canvas."]
        for item in items:
            ctx_str = f" ({item['context']})" if item.get("context") else ""
            start_str = ""
            if item.get("start"):
                try:
                    start_dt = datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
                    start_str = f" a las {start_dt.astimezone().strftime('%H:%M')}"
                except ValueError:
                    pass
            lines.append(f"{item['title']}{ctx_str}{start_str}.")
        return " ".join(lines)

    async def _handle_specific_evaluation(self, client, eval_name: str, course_hint: str) -> str:
        """Nota de una evaluación concreta: EC1, S1, parcial, etc."""
        courses = await self._cached("courses", lambda: client.get_courses())
        if not courses:
            return "No encontré cursos activos en Canvas."

        def _course_name(c) -> str:
            return c.get("name", "").split("|")[0].strip()

        if not course_hint:
            return (
                f"¿De qué curso querés la nota de {eval_name}? "
                "Decime el nombre, por ejemplo: 'nota de la EC1 en sistemas operativos'."
            )

        matched_course, options = self._resolve("course", course_hint, courses, _course_name)
        if matched_course is None:
            if not options:
                return (
                    f"No encontré el curso '{course_hint}' en Canvas. "
                    "Intentá con el nombre completo o parte del nombre."
                )
            self._remember_question("course", course_hint, options, {
                "handler": "specific_eval", "eval_name": eval_name,
            })
            return self._ask(course_hint, options, "curso")

        course_id = matched_course["id"]
        course_name = matched_course.get("name", "").split("|")[0].strip()
        cache_key = f"assignments_{course_id}"
        assignments = await self._cached(
            cache_key, lambda: client.get_assignments_with_scores(course_id)
        )

        if not assignments:
            return f"No hay evaluaciones cargadas en Canvas para {course_name}."

        # La sigla se expande antes de puntuar ("EC1" → "evaluacion continua 1")
        # y el número manda: la EC1 no es la EC2.
        best_a, options = self._resolve(
            "eval", eval_name, assignments, lambda a: a["title"], scorer=_score_eval,
        )
        if best_a is None:
            if not options:
                titulos = "\n".join(f"- {a['title']}" for a in assignments[:6])
                return (
                    f"No encontré una evaluación llamada '{eval_name}' en {course_name}. "
                    f"Las que figuran son:\n{titulos}"
                )
            self._remember_question("eval", eval_name, options, {
                "handler": "specific_eval", "course_hint": course_hint,
            })
            return self._ask(eval_name, options, "evaluación")

        if not best_a.get("graded") or best_a.get("score") is None:
            return f"{best_a['title']} en {course_name} aún no tiene nota cargada en Canvas."

        sc = float(best_a["score"])
        pts = best_a.get("points_possible")
        if pts:
            pct = sc / float(pts) * 100
            return (
                f"Tu nota en {best_a['title']} de {course_name} es "
                f"{sc:.1f} sobre {float(pts):.0f} puntos ({pct:.1f}%)."
            )
        return f"Tu nota en {best_a['title']} de {course_name} es {sc:.1f}."

    async def _handle_course_detail(self, client, query: str) -> str:
        """Detalle completo de un curso: nota, pendientes y anuncios."""
        grades       = await self._cached("grades",       lambda: client.get_course_grades())
        missing      = await self._cached("missing",      lambda: client.get_missing_submissions())
        announcements = await self._cached("announcements", lambda: client.get_announcements(limit=10))

        course, options = self._resolve("course", query, grades, lambda g: g["course"])
        if course is None:
            if not options:
                nombres = "\n".join(f"- {g['course']}" for g in grades[:8])
                return (
                    f"No encontré un curso llamado '{query}' en Canvas. "
                    f"Los que tenés activos son:\n{nombres}"
                )
            self._remember_question("course", query, options, {"handler": "course_detail"})
            return self._ask(query, options, "curso")

        lines = [f"Resumen de {course['course']} en Canvas."]

        # Nota
        if course.get("score") is not None:
            sc = float(course["score"])
            qualifier = "excelente" if sc >= 90 else "bien" if sc >= 80 else "regular" if sc >= 60 else "riesgo de desaprobación"
            lines.append(f"Tu nota actual es {sc:.1f} sobre 100, lo que está {qualifier}.")
        else:
            lines.append("Aún no hay nota cargada en Canvas para este curso.")

        # Entregas faltantes
        course_missing = [m for m in missing if self._names_overlap(m.get("course", ""), course["course"])]
        if course_missing:
            titles = ", ".join(m["title"] for m in course_missing[:3])
            lines.append(f"Tenés {len(course_missing)} entrega{'s' if len(course_missing) != 1 else ''} pendiente{'s' if len(course_missing) != 1 else ''}: {titles}.")
        else:
            lines.append("No tenés entregas faltantes en este curso.")

        # Anuncios
        course_anncs = [a for a in announcements if self._names_overlap(a.get("course", ""), course["course"])]
        if course_anncs:
            lines.append(f"Último anuncio: {course_anncs[0]['title']}.")

        texto = " ".join(lines)

        # Desglose por evaluación, si Canvas lo tiene. Marca explícitamente las que
        # todavía no tienen nota: "sin nota" es un dato, no un hueco que se calla.
        tabla = await self._evaluations_table(client, course.get("course_id"))
        return f"{texto}\n\n{tabla}" if tabla else texto

    async def _handle_evaluation_across_courses(self, client, eval_name: str) -> str:
        """La misma evaluación en todos los cursos: una fila por curso.

        Distingue tres estados y los dice explícitamente, porque son cosas
        distintas: tiene nota, existe pero sin calificar, y no existe en ese curso.
        """
        courses = await self._cached("courses", lambda: client.get_courses())
        if not courses:
            return "No encontré cursos activos en Canvas."

        legible = _expand_eval_alias(eval_name)
        filas = ["| Curso | Evaluación | Nota |", "|---|---|---|"]
        porcentajes: list[float] = []
        con_nota = sin_nota = no_figura = 0

        for c in courses:
            cid = c.get("id")
            nombre = c.get("name", "").split("|")[0].strip()
            if cid is None:
                continue
            try:
                items = await self._cached(
                    f"assignments_{cid}",
                    lambda cid=cid: client.get_assignments_with_scores(cid),
                )
            except Exception:
                items = []

            ranked = _rank(eval_name, items or [], lambda a: a.get("title", ""), scorer=_score_eval)
            mejor = ranked[0][1] if ranked and ranked[0][0] >= CLARIFY_LOW else None

            if mejor is None:
                filas.append(f"| {nombre} | — | no figura en este curso |")
                no_figura += 1
            elif mejor.get("graded") and mejor.get("score") is not None:
                sc = float(mejor["score"])
                pts = mejor.get("points_possible")
                nota = f"{sc:.1f}" + (f" / {float(pts):.0f}" if pts else "")
                if pts:
                    porcentajes.append(sc / float(pts) * 100)
                filas.append(f"| {nombre} | {mejor['title']} | {nota} |")
                con_nota += 1
            else:
                filas.append(f"| {nombre} | {mejor['title']} | sin nota registrada |")
                sin_nota += 1

        partes = [f"{legible.capitalize()} en tus {len(courses)} cursos de Canvas:", "", "\n".join(filas), ""]
        resumen = [f"{con_nota} con nota"]
        if sin_nota:
            resumen.append(f"{sin_nota} sin calificar todavía")
        if no_figura:
            resumen.append(f"{no_figura} donde no figura")
        cierre = ", ".join(resumen) + "."
        # El promedio solo si todas las notas son comparables entre sí; si no, se
        # dice por qué en vez de omitirlo en silencio.
        if porcentajes and len(porcentajes) == con_nota:
            cierre += f" Promedio de las calificadas: {sum(porcentajes)/len(porcentajes):.1f}%."
        elif con_nota:
            sin_escala = con_nota - len(porcentajes)
            cierre += (
                f" No calculo promedio: {sin_escala} de las calificadas no tiene "
                "puntaje máximo en Canvas, así que no son comparables."
            )
        partes.append(cierre)
        return "\n".join(partes)

    async def _evaluations_table(self, client, course_id) -> str:
        """Tabla de evaluaciones del curso con su nota, o "" si no hay datos."""
        if course_id is None:
            return ""
        try:
            items = await self._cached(
                f"assignments_{course_id}",
                lambda: client.get_assignments_with_scores(course_id),
            )
        except Exception:
            return ""
        if not items:
            return ""

        filas = ["| Evaluación | Nota |", "|---|---|"]
        for a in items:
            if a.get("graded") and a.get("score") is not None:
                pts = a.get("points_possible")
                nota = f"{float(a['score']):.1f}" + (f" / {float(pts):.0f}" if pts else "")
            else:
                nota = "sin nota registrada"
            filas.append(f"| {a['title']} | {nota} |")
        return "\n".join(filas)


    @staticmethod
    def _names_overlap(name_a: str, name_b: str) -> bool:
        """¿Estos dos nombres son el mismo curso?

        Un nombre vacío NO matchea con nada. `get_missing_submissions()` deja
        `course=""` cuando el id no está en el mapa, y con el `"" in b` de antes
        toda entrega huérfana se atribuía a CUALQUIER curso consultado — por eso
        aparecían entregas de un curso dentro del resumen de otro.

        Una o dos palabras en común no alcanzan: "Base de Datos Avanzada" y
        "Sistema de Gestión de Base de Datos" comparten dos y son cursos distintos.
        Se exige que lo compartido cubra CASI TODO el nombre más corto — los dos
        nombres salen de Canvas, así que lo normal es contención exacta; el ratio
        solo cubre variaciones menores de formato.
        """
        a, b = _norm(name_a), _norm(name_b)
        if not a or not b:
            return False
        if a in b or b in a:
            return True
        wa, wb = _significant_words(a), _significant_words(b)
        if not wa or not wb:
            return False
        return len(wa & wb) / min(len(wa), len(wb)) >= 0.75
