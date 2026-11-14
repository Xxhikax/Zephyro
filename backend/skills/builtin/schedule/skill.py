"""Schedule skill — wrapper sobre modules.schedule.ScheduleService."""
import asyncio
import json
import logging
import re
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

from core.request_context import RequestContext
from skills.base import BaseSkill, normalize_voseo


_CATEGORY_ALIASES = {
    # ── Exámenes universitarios ────────────────────────────────────
    "examen":          "uni_exam",
    "examenes":        "uni_exam",
    "exámenes":        "uni_exam",
    "parcial":         "uni_exam",
    "parciales":       "uni_exam",
    "final":           "uni_exam",
    "quiz":            "uni_exam",
    "quizz":           "uni_exam",
    "evaluacion":      "uni_exam",
    "evaluación":      "uni_exam",
    "evaluaciones":    "uni_exam",
    "prueba":          "uni_exam",
    "pruebas":         "uni_exam",
    # ── Tareas y entregas universitarias ──────────────────────────
    "tarea":           "uni_assignment",
    "tareas":          "uni_assignment",
    "entrega":         "uni_assignment",
    "entregas":        "uni_assignment",
    "entregar":        "uni_assignment",
    "homework":        "uni_assignment",
    "reporte":         "uni_assignment",
    "reportes":        "uni_assignment",
    "actividad":       "uni_assignment",
    "proyecto universitario": "uni_assignment",
    # ── Clases universitarias ──────────────────────────────────────
    "universidad":     "university",
    "uni":             "university",
    "clase":           "university",
    "clases":          "university",
    "materia":         "university",
    "materias":        "university",
    "asignatura":      "university",
    "asignaturas":     "university",
    "laboratorio":     "university",
    "laboratorios":    "university",
    "sistemas operativos": "university",
    "algoritmos":      "university",
    "cálculo":         "university",
    "calculo":         "university",
    "fisica":          "university",
    "física":          "university",
    # ── Estudio y cursos ──────────────────────────────────────────
    "estudio":         "course_study",
    "curso":           "course_study",
    # ── Proyectos ─────────────────────────────────────────────────
    "proyecto personal": "personal_project",
    "personal":        "personal_project",
    "proyecto cliente": "client_project",
    "cliente":         "client_project",
    "trabajo":         "client_project",
    "web":             "client_project",
    # ── Ejercicio ─────────────────────────────────────────────────
    "gym":             "exercise",
    "yoga":            "exercise",
    "correr":          "exercise",
    "running":         "exercise",
    "ejercicio":       "exercise",
    "deporte":         "exercise",
    "entrenamiento":   "exercise",
    "entrenar":        "exercise",
    # genéricos al final (orden importa: específicos primero)
    "proyecto":        "personal_project",
}


_WEEKDAYS = {
    "lunes":     0,
    "martes":    1,
    "miércoles": 2, "miercoles": 2,
    "jueves":    3,
    "viernes":   4,
    "sábado":    5, "sabado": 5,
    "domingo":   6,
}

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


_VERBS_SEMANTIC = (
    # imperativo directo: "agenda X", "pon X"
    r"pon|ponme|p[oó]nelo|agrega|agr[eé]game|a[ñn]ade|marca|recuerda|recu[eé]rdame|"
    r"program[aá]|programame|fija|registra|reg[ií]strame|apunta|ap[uú]ntame|anota|"
    r"agenda|ag[eé]ndame|agendar|"
    # subjuntivo (común en "necesito que agendes / pongas / anotes")
    r"agendes|agende|agenden|pongas|ponga|pongan|anotes|anote|anoten|"
    r"a[ñn]adas|a[ñn]ada|a[ñn]adan|"
    # frases compuestas comunes
    r"necesito\s+que\s+(?:agendes|agende|pongas|ponga|anotes|anote)"
)


def _infer_category(title_lower: str) -> str:
    for kw, cat in _CATEGORY_ALIASES.items():
        if kw in title_lower:
            return cat
    return "other"


def _next_weekday(target_wd: int) -> datetime:
    """Devuelve el datetime (00:00) del próximo día-de-semana indicado.
    Si hoy es ese día, devuelve hoy."""
    now = datetime.now()
    delta = (target_wd - now.weekday()) % 7
    return datetime.combine((now + timedelta(days=delta)).date(), dtime.min)


def _parse_explicit_date(text: str) -> Optional[datetime]:
    """Parsea fechas explícitas: '12 de mayo', '15 de junio', '15/05', '15-05-2026'.
    Retorna datetime a 00:00 o None si no se reconoce."""
    text = text.lower().strip()
    now = datetime.now()

    # "12 de mayo" / "el 15 de junio"
    m = re.search(r'(?:el\s+)?(\d{1,2})\s+de\s+(\w+)', text)
    if m:
        day = int(m.group(1))
        month_name = m.group(2).strip()
        month = _MESES.get(month_name)
        if month and 1 <= day <= 31:
            year = now.year
            dt = datetime(year, month, day)
            if dt < now:
                dt = datetime(year + 1, month, day)
            return dt

    # "15/05" o "15-05" (día/mes) o "15/05/2026"
    m = re.search(r'(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?', text)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year_str = m.group(3)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        if year_str:
            year = int(year_str)
            if year < 100:
                year += 2000
        else:
            year = now.year
        dt = datetime(year, month, day)
        if dt < now:
            dt = datetime(year + 1, month, day)
        return dt

    # "mayo" solo → 1 de mayo de este año (por si el LLM pone solo el mes)
    if text in _MESES:
        month = _MESES[text]
        dt = datetime(now.year, month, 1)
        if dt < now:
            dt = datetime(now.year + 1, month, 1)
        return dt

    return None


def _to_utc_ts(dt_local: datetime) -> float:
    return dt_local.astimezone(timezone.utc).timestamp()


def _format_event_line(start_ts_utc: float, title: str, category: str) -> str:
    local = datetime.fromtimestamp(start_ts_utc)
    return f"{local.strftime('%H:%M')} — {title} [{category}]"


def _ambiguity_note(title: str, category: str, end_hm: Optional[Tuple[int, int]], event_id: int) -> str:
    """Retorna advertencias si la información inferida es de baja confianza."""
    notes = []
    title_lower = title.lower().strip()

    # Títulos basura: contienen fillers o son solo palabras funcionales
    garbage_markers = [
        r"\bpor\s+favor\b",
        r"\bpara\s+el\s+d[ií]a\b",
        r"\bel\s+d[ií]a\b",
        r"\bporfa\b",
        r"\bag[eé]ndame\b",
        r"\bag[eé]ndeme\b",
        r"\bagenda\b",  # "agenda" solo como título es malo
        r"\bnecesito\b",
        r"\bquiero\b",
    ]
    if any(re.search(pat, title_lower) for pat in garbage_markers):
        notes.append("título dudoso")
    elif len(title_lower) < 4:
        notes.append("título dudoso")
    elif title_lower in {"clase", "curso", "reunión", "evento", "cosa", "tarea", "sesión", "sesion"}:
        notes.append("título genérico")

    if category == "other":
        notes.append("categoría sin definir")
    if end_hm is None:
        notes.append("hora de fin asumida (+1h)")
    if not notes:
        return ""
    return (
        f"\n⚠️ {', '.join(notes)}. "
        f"Decime \"modifica evento {event_id}\" para ajustarlo."
    )


# ──────────────────────────────────────────────────────────────────
# Parsers de fecha y hora (soportan formas naturales y estructuradas)
# ──────────────────────────────────────────────────────────────────

def _parse_target_date(text_lower: str) -> Optional[datetime]:
    """Devuelve datetime a 00:00 del día objetivo basado en keywords:
       'hoy', 'mañana', 'lunes', 'el martes', etc.
       También soporta fechas explícitas: '12 de mayo', '15/06'.
       None si no encuentra ninguno."""
    if "mañana" in text_lower or "manana" in text_lower:
        return datetime.combine((datetime.now() + timedelta(days=1)).date(), dtime.min)
    if "hoy" in text_lower:
        return datetime.combine(datetime.now().date(), dtime.min)
    for name, wd in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", text_lower):
            return _next_weekday(wd)
    return _parse_explicit_date(text_lower)


def _parse_hour_token(t: str) -> Optional[Tuple[int, int]]:
    """Parsea '7', '7:30', '7 y media', '7 y cuarto', '7 am', '15:00'.
       Devuelve (hour, minute) en formato 24h o None."""
    t = t.strip().lower()

    # "X y media" / "X y cuarto" / "X y treinta"
    m = re.search(r"(\d{1,2})\s*(?:y|con)\s*(media|cuarto|treinta|quince|cuarenta\s*y\s*cinco)", t)
    if m:
        hour = int(m.group(1))
        word = m.group(2)
        minute = {
            "media": 30, "treinta": 30,
            "cuarto": 15, "quince": 15,
            "cuarenta y cinco": 45,
        }.get(word.replace("  ", " "), 0)
        if ("pm" in t or "tarde" in t or "noche" in t) and hour < 12:
            hour += 12
        elif ("am" in t or "mañana" in t or "manana" in t) and hour == 12:
            hour = 0
        if hour > 23:
            return None
        return (hour, minute)

    # "HH:MM"
    m = re.search(r"(\d{1,2}):(\d{2})", t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if ("pm" in t or "tarde" in t or "noche" in t) and hour < 12:
            hour += 12
        elif ("am" in t or "mañana" in t or "manana" in t) and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        return (hour, minute)

    # "X y <numeric>" — ej: "7 y 10", "11 y 20", "1 y 40"
    m = re.search(r"(\d{1,2})\s*(?:y|con)\s*(\d{1,2})\b", t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if ("pm" in t or "tarde" in t or "noche" in t) and hour < 12:
            hour += 12
        elif ("am" in t or "mañana" in t or "manana" in t) and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        return (hour, minute)

    # "X am/pm" o solo "X" — busca el período (tarde/mañana) en cualquier posición del token
    # Esto soporta "2 en punto de la tarde" donde "en punto" separa el número del período.
    m = re.search(r"\b(\d{1,2})\b", t)
    if m:
        hour = int(m.group(1))
        if re.search(r"\b(?:pm|tarde|noche)\b", t):
            if hour < 12:
                hour += 12
        elif re.search(r"\b(?:am|ma[ñn]ana|manana)\b", t):
            if hour == 12:
                hour = 0
        if hour > 23:
            return None
        return (hour, 0)

    return None


_TIME_TOKEN_RE = (
    r"\d{1,2}(?::\d{2})?"
    r"(?:\s*(?:am|pm))?"
    r"(?:\s+y\s+(?:media|cuarto|treinta|quince|cuarenta\s*y\s*cinco|\d{1,2}))?"
    r"(?:\s+en\s+punto)?"                               # "2 en punto [de la tarde]"
    r"(?:\s+de\s+la\s+(?:ma[ñn]ana|tarde|noche))?"
)


def _infer_pm_if_needed(
    start: Tuple[int, int], end: Optional[Tuple[int, int]]
) -> Optional[Tuple[int, int]]:
    """If start is afternoon (>=13) and end looks like AM (< 12, < start), infer PM for end."""
    if end is None:
        return None
    if start[0] >= 13 and end[0] < 12:
        candidate = (end[0] + 12, end[1])
        if candidate[0] > start[0] or (candidate[0] == start[0] and candidate[1] >= start[1]):
            return candidate
    return end


def _parse_time_range(text_lower: str) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """Detecta rangos horarios en español. Devuelve (start_hm, end_hm); end_hm puede ser None."""

    # 1. Rango "de/desde X a/hasta Y" — admite "de la mañana/tarde/noche" en ambos lados
    m = re.search(
        rf"(?:de(?:\s+las?)?|desde(?:\s+las?)?)\s+"
        rf"({_TIME_TOKEN_RE})"
        rf"\s+(?:a(?:\s+las?)?|hasta(?:\s+las?)?)\s+"
        rf"({_TIME_TOKEN_RE})",
        text_lower,
    )
    if m:
        start = _parse_hour_token(m.group(1))
        end = _parse_hour_token(m.group(2))
        if start:
            return (start, _infer_pm_if_needed(start, end))

    # 2. "a las X hasta las Y" — rango con inicio en "a las" (sin de/desde)
    m = re.search(
        rf"a(?:\s+las?)?\s+({_TIME_TOKEN_RE})\s+hasta(?:\s+las?)?\s+({_TIME_TOKEN_RE})",
        text_lower,
    )
    if m:
        start = _parse_hour_token(m.group(1))
        end = _parse_hour_token(m.group(2))
        if start:
            return (start, _infer_pm_if_needed(start, end))

    # 3. Fin explícito suelto: "hasta las Y" / "termina a las Y" / "termina a Y"
    m_end = re.search(
        rf"(?:hasta(?:\s+las?)?|termina(?:\s+a(?:\s+las?)?)?)\s+({_TIME_TOKEN_RE})",
        text_lower,
    )
    end_hm = _parse_hour_token(m_end.group(1)) if m_end else None

    # 4. "a las X" o "empieza a las X" — inicio, luego combinar con end_hm del paso 3.
    # \b antes de "a" evita matchear la "a" interior de "hasta".
    m = re.search(
        rf"(?:empieza(?:\s+a(?:\s+las?)?)?|\ba(?:\s+las?)?)\s+({_TIME_TOKEN_RE})",
        text_lower,
    )
    if m:
        start = _parse_hour_token(m.group(1))
        if start:
            return (start, _infer_pm_if_needed(start, end_hm))

    # Si el paso 3 encontró solo un "hasta las X" sin start explícito, tratar como hora única.
    if end_hm:
        return (end_hm, None)

    # 5. Hora suelta (último intento, sin contexto de inicio/fin)
    m = re.search(r"\b(\d{1,2}(?::\d{2})?(?:\s*(?:am|pm))?)\b", text_lower)
    if m:
        start = _parse_hour_token(m.group(1))
        if start:
            return (start, None)

    return (None, None)


def _extract_title(text: str) -> str:
    """Limpia frases conversacionales del input para quedarse con el título.

    Soporta múltiples formas:
      'pon en mi agenda clase de cálculo el lunes de 7 a 9:30'
        → 'clase de cálculo'
      'Mi agenda en los horarios, este, pon el lunes que tengo clases de
       sistemas operativos de 7 a 9:30'
        → 'clases de sistemas operativos'
      'agenda gym hoy 23:00'
        → 'gym'
      'Por favor, para el día de mañana, agéndame. Tengo un curso
       llamado Sistemas Operativos de 7 de la mañana hasta las 11 y 10.'
        → 'Sistemas Operativos'
      'agéndame un curso que se llama Cálculo III el lunes de 8 a 10'
        → 'Cálculo III'

    Estrategia: si encuentra 'que tengo X', 'llamado X', o
    'que se llama X', extrae X. Si no, quita prefijos conversacionales
    y corta en el primer día/hora.
    """
    t = text.strip()

    # 0a. Strip wake word / direct address at start (STT includes device name)
    t = re.sub(r"^(?:zephyro|jarvis|hey\s+(?:zephyro|jarvis))\s*,?\s*", "", t, flags=re.IGNORECASE)

    # 0. Filler conversacional
    t = re.sub(
        r"\bmi\s+agenda\s+(?:en\s+(?:los?\s+)?horarios?|en\s+el\s+calendario)\s*,?\s*",
        "", t, flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tambi[eé]n|adem[aá]s|este|eh|um|pues|bueno|o\s+sea|por\s+favor|porfa)\s*,?\s*",
        "", t, flags=re.IGNORECASE,
    )
    t = t.strip(" ,.;:-—")

    # 0b. Quitar "para el día de", "el día" temprano
    t = re.sub(
        r"\bpara\s+el\s+d[ií]a\s+de\s+", "", t, flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bel\s+d[ií]a\s+(?:de\s+)?", "", t, flags=re.IGNORECASE,
    )
    # 0c. Quitar "para el próximo [día] [weekday]" al inicio (input STT sin verbo)
    t = re.sub(
        r"^para\s+el\s+pr[oó]ximo\s+(?:d[ií]a\s+)?"
        r"(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)?\s*[,;]?\s*",
        "", t, flags=re.IGNORECASE,
    )
    # 0d. Quitar "N [categoría]," al inicio: "dos exámenes,", "un examen,", "tres clases,"
    t = re.sub(
        r"^(?:un|una|dos|tres|cuatro|cinco|\d+)\s+"
        r"(?:ex[aá]menes?|examen|clases?|cursos?|materias?|parciales?|talleres?)\s*[,;]?\s*",
        "", t, flags=re.IGNORECASE,
    )

    # 0e. Multi-sentence: si la última oración es "Tengo/Hay X" sin marcadores temporales,
    # usarla como título directamente (ej: "...hasta las 2. Tengo clases de LP1.")
    _sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', t) if s.strip()]
    if len(_sentences) > 1:
        _last = _sentences[-1]
        _m_last = re.match(
            r'^(?:tengo|hay|son|es)\s+(?:(?:un|una|mis|los|las|unos|unas)\s+)?(.+?)\.?$',
            _last, re.IGNORECASE,
        )
        if _m_last:
            _cand = _m_last.group(1).strip()
            if not re.search(
                r'\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|'
                r'hoy|ma[ñn]ana|de\s+las?|a\s+las?|\d{1,2}:\d{2})\b',
                _cand, re.IGNORECASE,
            ):
                _cand = _clean_title(_cand)
                if len(_cand) >= 3:
                    return _cand

    # 1. Patrones de extracción directa (ordenados por especificidad)
    # Lookahead compartido: dispara en día de semana, hoy/mañana, o inicio de rango temporal.
    # "desde las N" incluye la variante con "las/la".
    _TIME_LOOKAHEAD = (
        r"(?=\s+\b(?:el\s+)?(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|hoy|ma[ñn]ana)\b|"
        r"\s+(?:de(?:\s+las?)?|desde(?:\s+las?)?|a(?:\s+las?)?)\s+\d|$)"
    )
    extraction_patterns = [
        # "tengo un curso llamado X"
        r"\b(?:tengo|hay|es)\s+(?:un|una)\s+\w+\s+(?:llamado|llamada|que\s+se\s+llama)\s+(.+?)"
        + _TIME_LOOKAHEAD,
        # "que se llama X" / "se llama X"
        r"\b(?:que\s+)?se\s+llama\s+(.+?)"
        + _TIME_LOOKAHEAD,
        # "tengo un/una clase/curso/materia [de] X" sin "llamado" — singular y plural
        # Ej: "tengo un curso redes y comunicaciones de 1:40"
        # Ej: "tengo clases de lenguajes de programación 1"
        r"\btengo\s+(?:(?:un|una|mis|unas|unos)\s+)?(?:clases?|cursos?|materias?|asignaturas?)\s+(?:de\s+)?(.+?)"
        + _TIME_LOOKAHEAD,
        # "que tengo X" / "que voy a tener X"
        r"\bque\s+(?:tengo|voy\s+a\s+tener)\s+(.+?)"
        + _TIME_LOOKAHEAD,
        # "llamado X" / "llamada X" (genérico)
        r"\bllamad[oa]\s+(.+?)"
        + _TIME_LOOKAHEAD,
    ]
    for pattern in extraction_patterns:
        m = re.search(pattern, t, flags=re.IGNORECASE)
        if m and m.group(1).strip():
            title = m.group(1).strip()
            # Limpiar artículos iniciales del título extraído
            title = re.sub(r"^\s*(?:el|la|los|las|un|una)\s+", "", title, flags=re.IGNORECASE).strip()
            # Limpiar palabras funcionales residuales
            title = _clean_title(title)
            if title:
                return title

    # 2. Fallback: quitar prefijos verbales y de agenda, cortar en día/hora
    t = re.sub(rf"^(?:{_VERBS_SEMANTIC})\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b(?:en|a|para)\s+(?:mi\s+|el\s+|la\s+)?(?:agenda|horario|calendario)\b\s*",
        "", t, flags=re.IGNORECASE,
    )
    # Fallback cuando la preposición se perdió en el habla ("añade mi agenda X"
    # en vez de "añade A mi agenda X"): la línea de arriba no dispara y
    # "mi agenda" sobrevive pegada al título; _clean_title() después solo
    # borraría la palabra "agenda" del medio, dejando "mi el curso..." (BUG).
    t = re.sub(r"^(?:mi|tu|su)\s+agenda\b\s*", "", t, flags=re.IGNORECASE)

    # 3. Cortar en el primer día/hora
    cut_patterns = [
        r"\b(?:el\s+)?(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b",
        r"\b(?:hoy|ma[ñn]ana)\b",
        r"\bde(?:\s+las?)?\s+\d",
        r"\ba(?:\s+las?)?\s+\d",
        r"\bdesde(?:\s+las?)?\s+\d",
        r"\b\d{1,2}:\d{2}\b",
    ]
    earliest = len(t)
    for pat in cut_patterns:
        m2 = re.search(pat, t, flags=re.IGNORECASE)
        if m2 and m2.start() < earliest:
            earliest = m2.start()
    title = t[:earliest].strip(" -—,.;:")

    # Limpieza final
    title = re.sub(r"^\s*(?:el|la|los|las)\s+", "", title, flags=re.IGNORECASE).strip()
    title = _clean_title(title)
    return title


def _clean_title(title: str) -> str:
    """Limpieza final del título: quita palabras funcionales y puntuación residual."""
    # Quitar "agéndame.", "agenda.", etc. residuales
    title = re.sub(
        r"\b(?:ag[eé]ndame|ag[eé]ndeme|agenda|ag[eé]ndamelo|ag[eé]ndemelo)\b\.?\s*",
        "", title, flags=re.IGNORECASE,
    )
    # Quitar "tengo", "tengo un curso", "necesito que" residuales
    title = re.sub(
        r"\b(?:tengo|necesito(?:\s+que)?|quiero(?:\s+que)?|voy\s+a\s+tener)\b\s*",
        "", title, flags=re.IGNORECASE,
    )
    # Quitar cláusulas relativas residuales al final: "que son", "y que tengo", "que", etc.
    title = re.sub(
        r"\s+(?:y\s+)?que(?:\s+(?:son|tengo|hay|voy|estoy|tenemos|tienen))?\s*$",
        "", title, flags=re.IGNORECASE,
    ).strip()
    # Quitar "en la/de la universidad/escuela/facultad" y lo que sigue (trailing phrase)
    title = re.sub(
        r"\s+(?:en|de)\s+(?:la\s+|el\s+)?(?:universidad|escuela|facultad|instituto|colegio)\b.*$",
        "", title, flags=re.IGNORECASE,
    ).strip()
    # Quitar verbos de tiempo al final: "redes y comunicaciones empieza" → "redes y comunicaciones"
    title = re.sub(
        r"\s+\b(?:empieza|termina|comienza|inicia|finaliza)(?:\s+.*)?$",
        "", title, flags=re.IGNORECASE,
    ).strip()
    # Quitar palabra de institución al inicio: "universidad Análisis" → "Análisis"
    title = re.sub(
        r"^(?:la\s+|el\s+)?(?:universidad|escuela|facultad|instituto|colegio)\s+",
        "", title, flags=re.IGNORECASE,
    ).strip()
    # Quitar el envoltorio "curso de" al inicio: el título es el nombre de la
    # materia ("curso de diseño de datos" → "diseño de datos"); la categoría
    # course_study ya marca que es un curso. "clase de X" se mantiene (pineado).
    title = re.sub(
        r"^(?:el\s+|un\s+|mi\s+)?curso\s+de\s+",
        "", title, flags=re.IGNORECASE,
    ).strip()
    # Colapsar espacios y puntuación sobrante
    title = re.sub(r"\s{2,}", " ", title)
    title = title.strip(" ,.;:-—.¿?¡!")
    return title


def _is_title_suspicious(title: str) -> bool:
    """Returns True if the regex-extracted title looks malformed and should trigger LLM fallback."""
    if not title or len(title.strip()) < 3:
        return True
    t = title.strip().lower()
    # Institution word not stripped (pattern 3 captured "universidad X" as title)
    if re.search(r"\b(?:universidad|escuela|facultad|instituto)\b", t):
        return True
    # Trailing time verb still present
    if re.search(r"\b(?:empieza|termina|comienza|inicia|finaliza)\s*$", t):
        return True
    # Contains "en la" + trailing word (residual location phrase)
    if re.search(r"\ben\s+la\s+\w+\s*$", t):
        return True
    # Is just a scheduling verb (title was never extracted)
    if re.search(r"^(?:ag[eé]ndame?|agenda|anota|a[ñn]ade|agrega)\s*$", t):
        return True
    # Possessive + article with no real noun in between ("mi el curso...") —
    # never valid Spanish; almost always residue of "agenda" stripped from the
    # middle of "mi agenda el X". Safety net → LLM fallback.
    if re.search(r"^(?:mi|tu|su)\s+(?:el|la|los|las)\b", t):
        return True
    # Contains numeric time expressions that should have been cut off
    if re.search(r"\b\d{1,2}\s*(?:y|con)\s*(?:media|\d{1,2})\b", t):
        return True
    if re.search(r"\ba\s+las?\s+\d", t):
        return True
    # Title is a temporal expression, not an event name
    if re.search(r"^(?:para\s+)?(?:el\s+)?(?:pr[oó]ximo|siguiente|este)\b", t, re.IGNORECASE):
        return True
    # Title is only a weekday/today/tomorrow
    if re.search(
        r"^\s*(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|hoy|ma[ñn]ana)\s*$",
        t, re.IGNORECASE,
    ):
        return True
    return False


# ──────────────────────────────────────────────────────────────────
# Skill class
# ──────────────────────────────────────────────────────────────────

# Patrón A: verbo → día → hora  (orden directo)
#   "agenda clase de cálculo el lunes de 7 a 9"
_SEMANTIC_PATTERN_A = re.compile(
    rf"(?:{_VERBS_SEMANTIC})"
    r".{0,80}"
    r"(?:\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b|"
    r"\b(?:hoy|ma[ñn]ana)\b)"
    r".{0,80}"
    r"\d{1,2}(?::\d{2})?",
    re.IGNORECASE | re.DOTALL,
)

# Patrón B: día → verbo → hora  (orden inverso, común en español natural)
#   "el lunes agenda un curso de 7 a 11"
#   "mañana ponme clase de cálculo a las 8"
_SEMANTIC_PATTERN_B = re.compile(
    r"(?:\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b|"
    r"\b(?:hoy|ma[ñn]ana)\b)"
    r".{0,80}"
    rf"(?:{_VERBS_SEMANTIC})"
    r".{0,80}"
    r"\d{1,2}(?::\d{2})?",
    re.IGNORECASE | re.DOTALL,
)

# Referencia unificada para compatibilidad
_SEMANTIC_PATTERN = _SEMANTIC_PATTERN_A

# Modificar: "modifica evento 13", "cambia el evento 13 para que termine a las 11:10"
# Incluye verbos de consulta/corrección que el usuario usa en hesitaciones de voz.
_MODIFY_PATTERN = re.compile(
    r"\b(?:modific[áa]|cambi[áa]|edit[áa]|actualiz[áa]|"
    r"verific[áa]|corrig[ée]|arregl[áa]|ajust[áa])\b",
    re.IGNORECASE,
)

# Referencia explícita a un evento existente: "evento 13", "evento #13",
# "evento número 13" (variante frecuente por voz/STT).
_EVENT_REF_PATTERN = re.compile(
    r"\bevento\s*(?:(?:n[úu]mero|n[úu]m\.?|nro\.?|#)\s*)?\d+\b", re.IGNORECASE
)

# Verbos de creación: si el mensaje los contiene, NO interpretar como modify implícito.
_CREATE_VERB_PATTERN = re.compile(
    r"\b(?:agenda|ag[ée]ndame|anot[áa]|añad[ée]|agreg[áa]|cre[áa]|pon[eé]?|programa)\b",
    re.IGNORECASE,
)

# Títulos sospechosos: cuando el LLM fallback intenta crear un evento con uno de estos
# títulos, casi siempre es un modify mal clasificado (hesitaciones, "verifica el evento N", etc).
_BAD_TITLE_PATTERN = re.compile(
    r"\bevento\s*#?\s*\d+|^\s*(?:verific|el\s+evento|la\s+clase\s+\d)",
    re.IGNORECASE,
)

# Eliminar: "elimina evento 13", "borra la clase de cálculo del lunes"
_DELETE_PATTERN = re.compile(
    r"\b(?:elimin[áa]|borr[áa]|quit[áa]|sac[áa])\b",
    re.IGNORECASE,
)

# Reprogramar: "reprográmame el gym", "no pude hacer ejercicio", "mueve el evento"
_RESCHEDULE_PATTERN = re.compile(
    r"\b(?:reprogram[aá]|reprogr[aá]mame|mueve|mov[eé]|pasalo|p[áa]salo|"
    r"no\s+pude\s+(?:hacer|ir)|no\s+puedo\s+(?:hacer|ir)|"
    r"cancela\s+y\s+mueve)\b",
    re.IGNORECASE,
)

# LLM-fallback hint, split in two halves: a temporal marker AND an agenda
# keyword. Long off-topic inputs that happen to contain a single keyword
# ("la tarea es una RedLand de dos niveles...") no longer false-match the
# schedule skill — see _schedule_llm_hint() below.
_SCHEDULE_HINT_TEMPORAL = re.compile(
    r"\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|hoy|ma[ñn]ana)\b"
    r"|\d{1,2}\s*(?:y|con)\s*\d{1,2}"
    r"|\d{1,2}(?::\d{2})?\s*(?:am|pm|de\s+la\s+(?:ma[ñn]ana|tarde|noche))"
    r"|\ba\s+las\s+\d{1,2}\b",
    re.IGNORECASE,
)
_SCHEDULE_HINT_KEYWORD = re.compile(
    r"\b(?:agenda|ag[eé]ndame|ag[eé]ndeme|agendar|agendes|horario|calendario|"
    r"clase|curso|eventos?|tengo\s+clase|tengo\s+curso|tengo\s+que\s+ir|"
    r"ex[aá]men|ex[aá]menes|parcial|parciales|tarea|entrega|pr[aá]ctica|trabajo)\b",
    re.IGNORECASE,
)


def _schedule_llm_hint(lower: str) -> bool:
    """True when the input looks schedule-shaped: at least one agenda keyword
    AND at least one temporal marker. Single occurrences of either are
    insufficient — they cause false matches on conversational text that
    happens to mention "tarea" or "lunes" in passing."""
    return bool(_SCHEDULE_HINT_KEYWORD.search(lower)) and bool(
        _SCHEDULE_HINT_TEMPORAL.search(lower)
    )


# Sustantivos de agenda — señal de que un verbo de modificación se refiere a un
# evento real y no a conversación ("cambiá la reunión" vs "cambia tu perspectiva").
_AGENDA_NOUN_RE = re.compile(
    r"\b(?:evento|reuni[oó]n|cita|recordatorio|gimnasio|gym|ejercicio|entreno|"
    r"estudio|sesi[oó]n|partido|turno)\b",
    re.IGNORECASE,
)


def _modify_has_agenda_context(lower: str) -> bool:
    """Un verbo de modificación/borrado/reprogramación (cambiá, verificá, borrá,
    movelo…) SÓLO es un comando de agenda si hay CONTEXTO de agenda: referencia a
    evento ("evento 13"), keyword de agenda (clase/curso/horario…), marcador
    temporal (lunes/hoy/"a las 7") o un sustantivo de evento (reunión/cita…).

    Sin ese contexto, esos verbos son conversación normal ("cambia tu
    perspectiva", "verifica esto", "no pude ir al cine") y NO deben rutear a la
    agenda. Mismo principio anti-falso-positivo que _schedule_llm_hint."""
    return bool(
        _EVENT_REF_PATTERN.search(lower)
        or _SCHEDULE_HINT_KEYWORD.search(lower)
        or _SCHEDULE_HINT_TEMPORAL.search(lower)
        or _AGENDA_NOUN_RE.search(lower)
    )


# Listar/consultar la agenda en lenguaje natural: "mostrame la agenda",
# "muéstrame mi calendario", "ver la agenda de la semana", "dame los eventos".
# El verbo de visualización va adelante (rompe el matching por prefijo "agenda "),
# por eso necesita su propio patrón. Cubre voseo (mostrá/mirá) y tuteo.
_LIST_AGENDA_RE = re.compile(
    r"\b(?:mostr[aá](?:me)?|mu[eé]strame|muestra|ve[ré]|mir[aá](?:me)?|dame|"
    r"dime|dec[ií]me|list[aá](?:me)?|list[ae]r|ens[eé][ñn][aá]me|chequea[rme]*|revis[aá])\b"
    r".{0,25}?\b(?:agenda|calendario|eventos?|horario|"
    r"(?:la|mi|esta)\s+semana|el\s+mes)\b",
    re.IGNORECASE,
)


# Backward compat: legacy regex exported for any test importing the old name.
# Mirrors the union of keyword + temporal so it doesn't surprise consumers.
_SCHEDULE_LLM_HINT = re.compile(
    r"(?:"
    r"\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|hoy|ma[ñn]ana)\b|"
    r"\b(?:agenda|ag[eé]ndame|ag[eé]ndeme|agendar|agendes|horario|calendario|clase|curso|"
    r"tengo\s+clase|tengo\s+curso|tengo\s+que\s+ir|"
    r"ex[aá]men|ex[aá]menes|parcial|parciales|tarea|entrega|pr[aá]ctica|trabajo)\b|"
    r"\d{1,2}\s*(?:y|con)\s*\d{1,2}|"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm|de\s+la\s+(?:ma[ñn]ana|tarde|noche))"
    r")",
    re.IGNORECASE,
)

# Recurrencia: "todos los lunes", "cada semana", "diariamente", "cada lunes"
_RECURRENCE_PATTERN = re.compile(
    r"\b(?:"
    r"todos\s+los\s+(?P<weekday>lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?|domingos?)|"
    r"cada\s+(?P<weekday2>lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)|"
    r"cada\s+semana|semanalmente|semanal|"
    r"todos\s+los\s+d[ií]as|diariamente|cada\s+d[ií]a"
    r")\b",
    re.IGNORECASE,
)

_WEEKDAY_TO_ABBREV = {
    "lunes": "MON", "martes": "TUE", "miércoles": "WED", "miercoles": "WED",
    "jueves": "THU", "viernes": "FRI", "sábado": "SAT", "sabado": "SAT",
    "sábados": "SAT", "sabados": "SAT", "domingos": "SUN", "domingo": "SUN",
}

_ABBREV_TO_WD_NUM: dict[str, int] = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}

# Multi-day recurrence: "todos los lunes y miércoles", "los lunes, miércoles y viernes"
_RECURRENCE_DAYS_RE = re.compile(
    r"\b(?:todos\s+los|cada|los)\s+"
    r"(?P<d1>lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?|domingos?)"
    r"(?:\s*[,]\s*(?:y\s+)?|\s+y\s+)"
    r"(?P<d2>lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?|domingos?)"
    r"(?:(?:\s*[,]\s*(?:y\s+)?|\s+y\s+)"
    r"(?P<d3>lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?|domingos?))?",
    re.IGNORECASE,
)

# "repite este patrón", "hacé lo mismo", "todo el mes", "todas las semanas", etc.
_REPEAT_PATTERN_RE = re.compile(
    r"\b(?:repet[ií](?:\s+este)?|repite(?:\s+este)?|"
    r"hac[eé]\s+lo\s+mismo|mismo\s+patr[oó]n|este\s+patr[oó]n|"
    r"para\s+todo\s+el\s+mes|todo\s+el\s+mes|"
    r"todas\s+las\s+semanas?|para\s+todas\s+las\s+semanas?|"
    r"el\s+resto\s+del\s+mes|expande(?:\s+esto)?|expand[ií]\s+esto)\b",
    re.IGNORECASE,
)


def _parse_recurrences(text_lower: str) -> list[str]:
    """Returns list of recurrence strings. Empty list = no recurrence detected."""
    if re.search(r"\btodos\s+los\s+d[ií]as\b|\bdiariamente\b|\bcada\s+d[ií]a\b", text_lower):
        return ["daily"]
    if re.search(r"\bcada\s+semana\b|\bsemanalmente\b|\bsemanal\b", text_lower):
        return ["weekly"]

    # Multi-day: "todos los lunes y miércoles"
    m = _RECURRENCE_DAYS_RE.search(text_lower)
    if m:
        seen: set[str] = set()
        result = []
        for gname in ("d1", "d2", "d3"):
            d = m.group(gname)
            if d:
                abbrev = _WEEKDAY_TO_ABBREV.get(d.lower())
                if abbrev and abbrev not in seen:
                    seen.add(abbrev)
                    result.append(f"weekly:{abbrev}")
        return result

    # Single day: "todos los lunes", "cada lunes"
    m = _RECURRENCE_PATTERN.search(text_lower)
    if m:
        wd = m.group("weekday") or m.group("weekday2")
        if wd:
            abbrev = _WEEKDAY_TO_ABBREV.get(wd.lower())
            if abbrev:
                return [f"weekly:{abbrev}"]
    return []


# Queries de notas/calificaciones/pendientes/entregas → pertenecen al Canvas skill, no
# a schedule. (Las clases/cursos/exámenes SÍ son de schedule — no se incluyen acá.)
_CANVAS_NOTE_HINT = re.compile(
    r'\b(?:notas?|calificaci[oó]n(?:es)?|pendiente[s]?\s+(?:en|de|para)|'
    r'nota\s+de|mis\s+notas?|cu[aá]l\s+es\s+mi\s+nota|'
    r'entregas?\s+(?:faltantes?|sin\s+entregar|pendientes?))\b',
    re.IGNORECASE,
)

# "hoy/mañana/esta noche tengo X" sin verbo de creación → es una consulta, no
# creación. Cubre ambos órdenes: "mañana tengo X" y "tengo X (para) mañana".
_DAY_TENGO_QUERY_RE = re.compile(
    r"\b(?:hoy|ma[ñn]ana|esta\s+(?:noche|tarde|ma[ñn]ana)|"
    r"el\s+(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo))\b"
    r"(?:.{0,80})?\btengo\b"
    r"|\btengo\b(?:.{0,60})?\b(?:hoy|ma[ñn]ana|esta\s+(?:noche|tarde|ma[ñn]ana)|"
    r"el\s+(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo))\b",
    re.IGNORECASE,
)


def _has_time_range(lower: str) -> bool:
    """True si el texto contiene una hora/rango horario explícito ("de 7 a 9",
    "a las 7", "7:30", "de 7 hasta las 9 y media"). Distingue un DICTADO de
    creación ("mañana tengo X de 7 a 9" → crear) de una CONSULTA sin hora
    ("qué tengo mañana" → listar). Reusa el parser de rango ya existente."""
    start, end = _parse_time_range(lower)
    return start is not None or end is not None


# Preguntas sobre horario de un evento ya existente: "a qué hora termina", "cuándo empieza"
_SCHEDULE_QUERY_RE = re.compile(
    r"\b(?:a\s+qu[eé]\s+hora|cu[aá]ndo|hasta\s+qu[eé]\s+hora)\s+"
    r"(?:termina|empieza|acaba|comienza|dura|es)\b"
    r"|(?:hora\s+de\s+(?:fin|inicio|término|comienzo))\b",
    re.IGNORECASE,
)


def _period_range(lower: str) -> "tuple[int, int] | None":
    """Retorna (hour_start_inclusive, hour_end_exclusive) para periodo del dia, o None."""
    if re.search(r'\bnoche\b', lower):
        return (18, 24)
    if re.search(r'\btarde\b', lower):
        return (12, 18)
    if re.search(r'\b(?:en\s+la\s+ma[ñn]ana|esta\s+ma[ñn]ana|de\s+la\s+ma[ñn]ana)\b', lower):
        return (0, 12)
    return None


_POMODORO_START_RE = re.compile(
    # Verbos: imperativo, presente, subjuntivo, pasado 1ª/3ª persona, infinitivo.
    # Cubre: "activa", "active" (subjuntivo), "activé", "activó" (pasado),
    # "inicia", "inició", "inicié", "empieza", "empezó", "empecé", "comenzó",
    # "comencé", "arranca", "arranqué", "comienza" (stem-change e→ie).
    r'\b(?:empiez[aá]|empez[aá]|empec[eé]|empez[óo]|'
    r'inici[aáeéoó]r?|'
    r'activ[aoeéó]r?|'
    r'comienz[aá]r?|comenz[aáoóé]r?|comenc[eé]|'
    r'arranc[aoaóé]r?|arranqu[eé]|'
    r'start|quiero|pon|lanz[aoaóé]|lanc[eé])'
    r'\s+(?:una?\s+)?(?:el\s+|la\s+)?(?:sesi[oó]n\s+de\s+)?(?:modo\s+)?pomodoro'
    r'(?:\s+de\s+(\d+)\s+min(?:utos?)?)?'
    r'(?:\s+(?:para|de)\s+(?P<task>.+))?',
    re.IGNORECASE,
)
_POMODORO_MODE_RE = re.compile(
    r'\b(modo\s+pomodoro|pomodoro\s+mode)\b', re.IGNORECASE,
)
_POMODORO_STOP_RE = re.compile(
    r'\b(par[aá]|det[eé]n|deten[eé]|stop|cancela)\s+(el\s+)?pomodoro', re.IGNORECASE,
)
# _POMODORO_BREAK_RE only matches "descanso corto/largo" as standalone commands,
# NOT as part of a config response — the session state guard handles that.
_POMODORO_BREAK_RE = re.compile(
    r'^\s*(descanso|break)\s*(corto|largo)?\s*$', re.IGNORECASE,
)
_POMODORO_STATUS_RE = re.compile(
    r'\b(?:c[oó]mo\s+va|estado\s+del?|cu[aá]nto\s+falta)\s+(?:el\s+)?pomodoro\b',
    re.IGNORECASE,
)
# ── In-session controls (only meaningful with an active session) ─────────────
_POMODORO_PAUSE_RE = re.compile(
    r'\b(?:pausa|paus[aá]r?|paus[aá]me)\s+(?:el\s+|al\s+)?pomodoro\b'
    r'|^\s*pausa(?:\s+esto|\s+un\s+momento)?\s*$'
    r'|^\s*espera\s+(?:un\s+momento|un\s+segundo|un\s+rato|por\s+ahora)\s*$'
    r'|\bdetente\s+un\s+momento\b',
    re.IGNORECASE,
)
_POMODORO_RESUME_RE = re.compile(
    r'\b(?:reanud[aá]r?|continu[aá]r?|retom[aá]r?)\s+(?:el\s+|al\s+)?pomodoro\b'
    r'|^\s*(?:reanuda|reanudar|sigue|continúa|continua|continuar|retoma|sigamos)\s*$'
    r'|\bvolvamos\s+al\s+pomodoro\b',
    re.IGNORECASE,
)
_POMODORO_EXTEND_RE = re.compile(
    r'\b(?:añ[aá]de|agreg[aá]|extiende|extender|dame|sum[aá]|p[oó]n)'
    r'(?:me)?\s+(\d+)\s*(?:min(?:utos?)?|m)\s*(?:m[aá]s)?'
    r'(?:\s+(?:al|del?)\s+(?:pomodoro|ciclo|trabajo|descanso))?',
    re.IGNORECASE,
)
_POMODORO_EXTEND_CYCLE_RE = re.compile(
    r'\b(?:añ[aá]de|agreg[aá]|sum[aá])\s+(?:un\s+|otro\s+)?ciclo'
    r'(?:\s+(?:m[aá]s|extra|al\s+final))?',
    re.IGNORECASE,
)
_POMODORO_EXTEND_CONFIRM_CURRENT_RE = re.compile(
    r'^\s*(?:extiende|este\s+ciclo|actual|extiéndelo)\s*$',
    re.IGNORECASE,
)
_POMODORO_EXTEND_CONFIRM_NEW_RE = re.compile(
    r'^\s*(?:ciclo\s+nuevo|nuevo\s+ciclo|otro\s+ciclo|nuevo|al\s+final)\s*$',
    re.IGNORECASE,
)
_POMODORO_SKIP_BREAK_RE = re.compile(
    r'\b(?:salt[aá]r?|skip|omit[ie]r?)\s+(?:el\s+|este\s+)?descanso\b'
    r'|^\s*(?:siguiente\s+ciclo\s+ya|próximo\s+ciclo\s+ya|proximo\s+ciclo\s+ya)\s*$',
    re.IGNORECASE,
)
# Loose hint: cualquier mención de "pomodoro" o "sesión de pomodoro" sin verbo.
# Atrapa frases sin estructura que el LLM general respondería con alucinaciones.
# Ej: "una sesión de Pomodoro.", "el pomodoro de hoy", "pomodoro de 45 min".
_POMODORO_HINT_RE = re.compile(
    r'\b(?:una?\s+)?(?:sesi[oó]n\s+de\s+)?(?:modo\s+)?pomodoros?\b',
    re.IGNORECASE,
)

# ── Focus / Study mode / Whitelist commands ────────────────────────────────────
_STUDY_MODE_START_RE = re.compile(
    r'\b(?:act[ií]v[aá](?:me)?r?|entr[aá]r?\s+en|pon(?:me)?\s+(?:en\s+)?|'
    r'inici[aá]r?|empiez[aá]|quier[eo](?:\s+el)?)\s*'
    r'(?:el\s+)?modo\s+estudio\b'
    r'|^(?:(?:Zephyro|oye|hey|eh)[\s,]+)?modo\s+estudio(?:\s+(?:ya|por\s+favor|now))?\s*$',
    re.IGNORECASE,
)
_STUDY_MODE_STOP_RE = re.compile(
    r'\b(?:sal(?:[ií]r?)?\s+del?|desactiv[aá]r?|par[aá]r?|termina[rm]?)\s+'
    r'(?:el\s+)?modo\s+estudio\b',
    re.IGNORECASE,
)
_WHITELIST_SHOW_RE = re.compile(
    r'\b(?:mu[eé]strame?|ver?|lista[rm]?|qu[eé]\s+(?:apps?|aplicaciones?)\s+'
    r'(?:tengo|están?|son))\s+(?:la\s+)?whitelist\b'
    r'|\bqu[eé]\s+(?:apps?|aplicaciones?)\s+(?:(?:tengo|están?|son)\s+)?permitidas?\b'
    r'|^\s*whitelist\s*$',
    re.IGNORECASE,
)
_WHITELIST_ADD_RE = re.compile(
    r'\bagr[eé]g[aá](?:me|r)?\s+(.+?)\s+(?:a\s+(?:la\s+)?(?:whitelist|lista\s+(?:blanca|permitida))'
    r'|como\s+(?:app\s+)?permitida?)',
    re.IGNORECASE,
)
_WHITELIST_PERMANENT_RE = re.compile(
    r'para\s+siempre|permanentemente|de\s+forma\s+permanente|para\s+otras\s+sesiones?',
    re.IGNORECASE,
)
_FOCUS_REPORT_RE = re.compile(
    r'\b(?:reporte?\s+de\s+(?:distrac(?:ci[oó]n|ciones?)|foco)|'
    r'cu[aá]nto\s+tiempo\s+(?:me\s+)?distra[ij]e|'
    r'resumen\s+(?:de\s+)?(?:la\s+)?sesi[oó]n\s+(?:de\s+estudio|pomodoro)'
    r'|mis\s+distracciones?)\b',
    re.IGNORECASE,
)
# Individual extractors for config responses.
# Each pattern supports both orders: "45 min de trabajo" and "trabajo serían 45 minutos".
# Also handles Spanish word numbers for cycles (uno, dos, tres, cuatro, cinco).
_POMO_CFG_CYCLES_RE = re.compile(
    r'(\d+)\s*ciclos?'
    r'|(?:uno|dos|tres|cuatro|cinco|seis|siete|ocho)\s+ciclos?'
    r'|(?:el\s+)?(?:uno|dos|tres|cuatro|cinco|seis|siete|ocho)\s+ciclos?',
    re.IGNORECASE,
)
_WORD_CYCLE_MAP = {
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
}
_POMO_CFG_WORK_RE   = re.compile(
    r'(\d+)\s*min(?:utos?)?\s+(?:de\s+)?(?:trabajo|estudio)'
    r'|(?:trabajo|estudio)\s+(?:\w+\s+){0,3}?(\d+)\s*min(?:utos?)?',
    re.IGNORECASE,
)
_POMO_CFG_BREAK_RE  = re.compile(
    r'(\d+)\s*min(?:utos?)?\s+(?:de\s+)?descanso'
    r'|descanso\s+(?:\w+\s+){0,3}?(\d+)\s*min(?:utos?)?',
    re.IGNORECASE,
)


def _hour_to_natural(hour: int, minute: int = 0) -> str:
    """Convierte hora a texto español hablado para TTS."""
    if hour < 12:
        period = "de la mañana"
    elif hour < 18:
        period = "de la tarde"
    else:
        period = "de la noche"
    h = hour if hour <= 12 else hour - 12
    if h == 0:
        h = 12
    if minute == 0:
        return f"las {h} {period}"
    elif minute == 30:
        return f"las {h} y media {period}"
    elif minute == 15:
        return f"las {h} y cuarto {period}"
    return f"las {h} y {minute} {period}"

# ── University category classifier ────────────────────────────────

_UNI_SIGNAL_RE = re.compile(
    r'\b(?:universidad|uni|materia|asignatura|semestre|facultad|carrera|'
    r'profesor|clase|clases|cursos?|parcial|examen|tarea[s]?|entrega|'
    r'reporte|pr[aá]ctica|laboratorio|homework|quiz|evaluaci[oó]n|prueba)\b',
    re.IGNORECASE,
)

_UNI_CLASSIFY_PROMPT = (
    "Clasifica este evento universitario en UNA categoría.\n"
    "Responde SOLO con una de: university, uni_exam, uni_assignment\n\n"
    "- university: clase, materia, asignatura, práctica, laboratorio, ir a clases\n"
    "- uni_exam: examen, parcial, final, quiz, evaluación, prueba\n"
    "- uni_assignment: tarea, entrega, proyecto, reporte, homework, actividad a entregar\n\n"
    "Evento: \"{title}\"\n"
    "Contexto: \"{input}\"\n\n"
    "Categoría:"
)


# ── Schedule LLM Extraction Prompt ─────────────────────────────────

_SCHEDULE_EXTRACTION_PROMPT = """Eres un extractor de intenciones de agenda/calendario en español.
Analiza el mensaje del usuario y devuelve SOLO un JSON válido (sin markdown, sin explicaciones).

ACCIONES (campo "action"):
- "create": crear eventos nuevos. Incluye verbos explícitos (agenda, agéndame, anota, añade, agrega, programa, pon, crea) Y TAMBIÉN inputs IMPLÍCITOS donde el usuario describe un evento con fecha + hora sin verbo explícito (ej: "para el próximo día lunes, dos exámenes, X de H1 a H2 y luego Y de H3 a H4").
- "list": listar/consultar agenda. TAMBIÉN cuando el usuario dice "hoy/mañana/esta noche tengo X" sin verbo de creación explícito (agenda/pon/añade/anota/etc.) — es una consulta de confirmación, NO una creación
- "modify": modificar evento existente. SIEMPRE que el mensaje mencione "evento N" (con N número) sin un verbo de creación explícito, la acción es "modify", NO "create".
- "delete": eliminar (elimina, borra, quita)
- "none": si no es comando de agenda

FORMATO DE RESPUESTA:
Si el usuario describe UN solo evento, responde con el objeto JSON directamente.
Si detectas MÚLTIPLES eventos (separados por ". Y", "también", "por la tarde", "el segundo", "luego", etc.), responde con {"action":"create","events":[{evento1},{evento2},...]}.

CADA EVENTO individual tiene este schema:
{
  "title": "título del evento (string obligatorio para create)",
  "date_keyword": "hoy|mañana|lunes|martes|miércoles|jueves|viernes|sábado|domingo|<fecha explícita>|null",
  "date_keyword_help": "Si el usuario dice '12 de mayo', 'el 15 de junio', '15/05', pon el texto literal como date_keyword. Si dice 'hoy', 'mañana', o un día de la semana, usa esa palabra.",
  "start_hour": int 0-23 o null,
  "start_minute": int 0-59 (default 0),
  "end_hour": int 0-23 o null,
  "end_minute": int 0-59 (default 0),
  "category": "university|uni_exam|uni_assignment|course_study|personal_project|client_project|exercise|other",
  "event_id": int o null,
  "notes": string o null
}

Para un SOLO evento, agrega "action" al objeto raíz. Para MÚLTIPLES, "action" y "events" al objeto raíz.
Ejemplo un solo evento:
{"action":"create","title":"Sistemas Operativos","date_keyword":"mañana","start_hour":7,"start_minute":0,"end_hour":11,"end_minute":0,"category":"university","event_id":null,"notes":null}

Ejemplo MÚLTIPLES eventos:
{"action":"create","events":[
  {"title":"Análisis y Modelamiento de Software","date_keyword":"martes","start_hour":7,"start_minute":0,"end_hour":9,"end_minute":30,"category":"university"},
  {"title":"Redes y Comunicaciones","date_keyword":"martes","start_hour":13,"start_minute":40,"end_hour":17,"end_minute":40,"category":"university"}
]}

REGLAS DE FECHA:
- "para el próximo [día] [weekday]" → date_keyword: el día de semana. Ej: "para el próximo día lunes" → "lunes".
- "dos exámenes, X ... y luego Y" → dos eventos separados, ambos con category="uni_exam".
- "el primer/segundo/otro examen/curso" → hace referencia al N-ésimo evento de la lista, no es título.

REGLAS DE TÍTULO (críticas):
- "title" debe ser SOLO el nombre de la actividad/evento.
- NO incluir frases temporales ("de 7 a 9", "mañana", "desde las", "hasta las").
- NO incluir "que tengo", "que son", "y que", "que se llama", "la primera clase", "y por la tarde", "el primer curso", "el segundo curso", "este es de", etc.
- Si hay "se llama X" o "llamado X", X es el título. Si hay "tengo una clase de X", X es el título.
- Para categoría: si menciona "universidad", "clase", "clases", "curso", usar "university". Si menciona "examen", "exámenes", "parcial", "final", "quiz", usar "uni_exam". Si menciona "tarea", "entrega", "reporte", "homework", usar "uni_assignment".

REGLAS DE TIEMPO (críticas):
- start: "a las X", "empieza a las X", "desde las X", "de X", "de la X"
- end: "termina a las Y", "hasta las Y", "hasta Y", o el segundo número en un rango
- Rango "de X a Y" o "desde X hasta Y" → start_hour=X, end_hour=Y. SIEMPRE extraer ambos.
- "X y media" → minute=30. "X y cuarto" → minute=15. "X y N" (numérico) → minute=N.
- "de la mañana" → 24h igual (7 mañana = 7). "de la tarde/noche" → si hour<12, sumar 12.
- Formato 24h en el JSON. Si no hay hora explícita → null.

EJEMPLOS:
"mañana tengo una clase de la universidad que se llama análisis de software de 7 de la mañana a 9 y media"
→ {"action":"create","title":"análisis de software","date_keyword":"mañana","start_hour":7,"start_minute":0,"end_hour":9,"end_minute":30,"category":"university","event_id":null,"notes":null}

"tengo un curso de la universidad mañana que se llama análisis y diseño de software, empieza a las 7 de la mañana y termina a las 9 y media"
→ {"action":"create","title":"análisis y diseño de software","date_keyword":"mañana","start_hour":7,"start_minute":0,"end_hour":9,"end_minute":30,"category":"university","event_id":null,"notes":null}

"mañana tengo clase de redes y telecomunicaciones desde las 1 y 40 de la tarde hasta las 5 y 40 de la tarde"
→ {"action":"create","title":"redes y telecomunicaciones","date_keyword":"mañana","start_hour":13,"start_minute":40,"end_hour":17,"end_minute":40,"category":"university","event_id":null,"notes":null}

"tengo examen de cálculo el lunes a las 9"
→ {"action":"create","title":"cálculo","date_keyword":"lunes","start_hour":9,"start_minute":0,"end_hour":null,"end_minute":0,"category":"uni_exam","event_id":null,"notes":null}

"entregar tarea de física mañana a las 11"
→ {"action":"create","title":"tarea de física","date_keyword":"mañana","start_hour":11,"start_minute":0,"end_hour":null,"end_minute":0,"category":"uni_assignment","event_id":null,"notes":null}

"el evento 15, empieza a las 7 y termina a las 9 y media"
→ {"action":"modify","title":null,"date_keyword":null,"start_hour":7,"start_minute":0,"end_hour":9,"end_minute":30,"category":null,"event_id":15,"notes":null}

"modifica evento 15 categoría universidad"
→ {"action":"modify","title":null,"date_keyword":null,"start_hour":null,"start_minute":0,"end_hour":null,"end_minute":0,"category":"university","event_id":15,"notes":null}

"para el 12 de mayo necesito que me agendes un curso que se llama análisis de software, empieza a las 7 de la mañana y termina a las 9 y media"
→ {"action":"create","title":"análisis de software","date_keyword":"12 de mayo","start_hour":7,"start_minute":0,"end_hour":9,"end_minute":30,"category":"university","event_id":null,"notes":null}

"el 15 de junio tengo examen de cálculo a las 10"
→ {"action":"create","title":"cálculo","date_keyword":"15 de junio","start_hour":10,"start_minute":0,"end_hour":null,"end_minute":0,"category":"uni_exam","event_id":null,"notes":null}

"para el próximo día lunes, dos exámenes, diseño de base de datos de 12 a 1 y 40 de la tarde y luego análisis y modelamiento de software, este es de 1 y 40 a 3 y 20 de la tarde"
→ {"action":"create","events":[{"title":"Diseño de Base de Datos","date_keyword":"lunes","start_hour":12,"start_minute":0,"end_hour":13,"end_minute":40,"category":"uni_exam"},{"title":"Análisis y Modelamiento de Software","date_keyword":"lunes","start_hour":13,"start_minute":40,"end_hour":15,"end_minute":20,"category":"uni_exam"}]}

Devuelve SOLO el JSON."""


def _parse_schedule_llm_json(raw: str) -> Optional[dict]:
    """Parsea la respuesta JSON del LLM para scheduling. Retorna dict con 'events' opcional."""
    original = raw
    m = re.search(r'```(?:json)?\s*(\{.*?\}|\s*\[.*?\])\s*```', raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        start = raw.find('{')
        if start == -1:
            start = raw.find('[')
        if start != -1:
            opener = raw[start]
            closer = '}' if opener == '{' else ']'
            end = raw.rfind(closer)
            if end > start:
                raw = raw[start:end + 1]
    try:
        result = json.loads(raw)
        if not isinstance(result, (dict, list)):
            logger.debug("LLM JSON no es dict/list. raw=%r", original[:200])
            return None
        return result
    except json.JSONDecodeError as exc:
        logger.debug("LLM JSON parse error: %s. raw=%r", exc, original[:200])
        return None


class ScheduleSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._service = None
        self._llm_cache: dict[str, dict] = {}
        self._llm_cache_lock = asyncio.Lock()
        # Pomodoro setup state: user_id → True while waiting for config response
        self._pending_pomodoro_setup: dict[str, bool] = {}
        # Stores the task name across multi-turn config (e.g., user sends task first, then numerics)
        self._pending_pomodoro_task: dict[str, str] = {}
        # Pending extend confirmation: user_id → minutes the user asked for, waiting
        # on a follow-up "extiende este ciclo" vs "ciclo nuevo" decision.
        self._pending_pomodoro_extend: dict[str, int] = {}
        # Last created pattern context per user, for "repite este patrón" follow-ups
        self._last_created_series: dict[str, dict] = {}

    def _get_service(self):
        if self._service is None:
            from modules.schedule import ScheduleService
            self._service = ScheduleService()
        return self._service

    # ── Matching ──────────────────────────────────────────────────

    def matches_command(self, user_input: str, ctx=None) -> bool:
        lower = normalize_voseo(user_input.lower().strip())
        if "canvas" in lower:
            return False
        # Vision Q&A takes priority when the user is asking about their screen.
        # Local import avoids any circular dependency at module-load time.
        try:
            from skills.builtin.vision_query.skill import _VISION_QUERY_RE
            if _VISION_QUERY_RE.search(lower):
                return False
        except ImportError:
            pass
        # Pomodoro config response: grab it before any other routing
        user_id = (ctx.user_role if ctx and hasattr(ctx, "user_role") else None) or "admin"
        if self._pending_pomodoro_setup.get(user_id):
            return True
        # Pomodoro extend confirmation pending — capture "extiende" / "ciclo nuevo"
        # responses that wouldn't match any other regex.
        if self._pending_pomodoro_extend.get(user_id):
            return True

        for trig in self.metadata.triggers:
            t = trig.lower()
            if lower == t or lower.startswith(t):
                return True

        if self._matches_pomodoro(lower):
            return True

        if self._matches_focus_control(lower):
            return True

        if self._matches_semantic(lower):
            return True
        # Listado en lenguaje natural: "mostrame la agenda de la semana"
        if _LIST_AGENDA_RE.search(lower):
            return True
        # "repite este patrón" / "todo el mes" — only when we have stored context
        if _REPEAT_PATTERN_RE.search(lower) and self._last_created_series.get(user_id):
            return True
        # 3. LLM fallback hint: now requires BOTH an agenda keyword AND a
        #    temporal marker — single isolated keywords ("la tarea es...",
        #    "el lunes voy a...") no longer false-match.
        if _schedule_llm_hint(lower) and not _CANVAS_NOTE_HINT.search(lower):
            return True
        return False

    def match_confidence(self, user_input: str) -> float:
        """
        Confianza granular para el ClarificationGate. Refleja la misma cascada
        que ``matches_command`` pero con scores: cuanto más específico el match,
        mayor la confianza. El hint difuso (keyword+temporal) devuelve un score
        medio-bajo a propósito, para que el gate pueda preguntar si compite con
        otra interpretación (ej. "poné cálculo" → ¿agendar o reproducir?).
        """
        lower = normalize_voseo(user_input.lower().strip())
        if "canvas" in lower:
            return 0.0
        try:
            from skills.builtin.vision_query.skill import _VISION_QUERY_RE
            if _VISION_QUERY_RE.search(lower):
                return 0.0
        except ImportError:
            pass

        # Trigger exacto o prefijo → certeza máxima
        for trig in self.metadata.triggers:
            t = trig.lower()
            if lower == t or lower.startswith(t):
                return 1.0

        # Regex de dominio específicos → alta
        if self._matches_pomodoro(lower) or self._matches_focus_control(lower):
            return 0.9

        # Patrones semánticos (verbo→día→hora, modify, delete) → media-alta
        if self._matches_semantic(lower):
            return 0.7

        # Listado en lenguaje natural ("mostrame la agenda") → alta
        if _LIST_AGENDA_RE.search(lower):
            return 0.85

        # Hint difuso: keyword de agenda + marcador temporal, sin estructura →
        # media-baja, candidato a clarificación.
        if _schedule_llm_hint(lower) and not _CANVAS_NOTE_HINT.search(lower):
            return 0.45

        return 0.0

    def _matches_semantic(self, lower: str) -> bool:
        # Patrones de creación con estructura (verbo→día→hora) → comando directo.
        if _SEMANTIC_PATTERN_A.search(lower) or _SEMANTIC_PATTERN_B.search(lower):
            return True
        # Verbos de modificación/borrado/reprogramación: sólo son comando de
        # agenda si hay contexto de agenda. "cambia"/"verifica"/"borra" sueltos en
        # conversación ("cambia tu perspectiva") ya NO falsean el match.
        if (_MODIFY_PATTERN.search(lower)
                or _DELETE_PATTERN.search(lower)
                or _RESCHEDULE_PATTERN.search(lower)):
            return _modify_has_agenda_context(lower)
        return False

    def _matches_focus_control(self, lower: str) -> bool:
        return (
            bool(_STUDY_MODE_START_RE.search(lower))
            or bool(_STUDY_MODE_STOP_RE.search(lower))
            or bool(_WHITELIST_SHOW_RE.search(lower))
            or bool(_WHITELIST_ADD_RE.search(lower))
            or bool(_FOCUS_REPORT_RE.search(lower))
        )

    def _matches_pomodoro(self, lower: str) -> bool:
        return (
            bool(_POMODORO_START_RE.search(lower))
            or bool(_POMODORO_MODE_RE.search(lower))
            or bool(_POMODORO_STOP_RE.search(lower))
            or bool(_POMODORO_BREAK_RE.search(lower))
            or bool(_POMODORO_STATUS_RE.search(lower))
            or bool(_POMODORO_PAUSE_RE.search(lower))
            or bool(_POMODORO_RESUME_RE.search(lower))
            or bool(_POMODORO_EXTEND_RE.search(lower))
            or bool(_POMODORO_EXTEND_CYCLE_RE.search(lower))
            or bool(_POMODORO_SKIP_BREAK_RE.search(lower))
            # Loose hint: mención de "pomodoro" sin verbo. Atrapa "una sesión de pomodoro"
            # y deja que el LLM extractor decida la acción correcta.
            or bool(_POMODORO_HINT_RE.search(lower))
        )

    # ── LLM Extraction ────────────────────────────────────────────

    async def _run_primary_extraction(self, full_prompt: str) -> Optional[dict]:
        """Pasada 1: usa el cloud LLM por defecto del orchestrator (típicamente
        Anthropic Haiku/Sonnet). Devuelve dict parseado o None si falla."""
        llm_manager = None
        try:
            import web_app as _wa
            orch = _wa.app.state.orchestrator if hasattr(_wa, 'app') else None
            if orch is not None:
                llm_manager = orch.llm
        except Exception:
            pass

        if llm_manager is None or not hasattr(llm_manager, 'generate'):
            return None

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: llm_manager.generate(
                        full_prompt,
                        force_cloud=True,
                        force_local=False,
                    )
                ),
                timeout=12.0,
            )
            return _parse_schedule_llm_json(raw)
        except asyncio.TimeoutError:
            logger.warning("LLM schedule extraction (primary) timed out")
            return None
        except Exception as exc:
            logger.warning("LLM schedule extraction (primary) error: %s", exc)
            return None

    async def _run_deepseek_fallback(self, full_prompt: str) -> Optional[dict]:
        """Pasada 2: DeepSeek v4 flash con JSON mode estricto, vía
        call_deepseek_flash_json (API directa, sin OpenCode CLI).

        Se usa cuando la primera pasada falla, devuelve None, o produce
        action=none aunque el input claramente menciona agenda. El JSON mode
        del modelo hace la salida más estable que el modo libre del primario.
        """
        try:
            from core.llm_helpers import call_deepseek_flash_json
        except ImportError:
            return None
        try:
            raw = await call_deepseek_flash_json(full_prompt, timeout=10.0, max_tokens=2048)
            if not raw:
                return None
            return _parse_schedule_llm_json(raw)
        except Exception as exc:
            logger.warning("LLM schedule extraction (deepseek fallback) error: %s", exc)
            return None

    def _is_useful_extraction(self, data: Optional[dict]) -> bool:
        """¿El resultado del LLM tiene contenido procesable?

        Sirve para decidir si activar el fallback de DeepSeek. Útil =
        action != none (create/list/modify/delete) o multi-evento.
        """
        if not isinstance(data, dict):
            return False
        events = data.get("events")
        if isinstance(events, list) and len(events) >= 1:
            return True
        action = data.get("action", "none")
        return action in {"create", "list", "modify", "delete"}

    async def _extract_schedule_intent(self, user_input: str, user_id: str) -> Optional[str]:
        """Extrae intención de agenda con LLM cuando los regex no matchean.

        Flujo de fallbacks:
          1. Cloud LLM por defecto (Anthropic Haiku/Sonnet vía LLMManager).
          2. Si la 1 falla o devuelve action=none → DeepSeek v4 flash (JSON mode).
          3. Si ambas fallan → None (caller decide qué hacer).
        """
        cache_key = user_input.lower().strip()

        async with self._llm_cache_lock:
            cached = self._llm_cache.get(cache_key)
        if cached is not None:
            if isinstance(cached, list):
                results = []
                for ev_data in cached:
                    r = self._dispatch_llm_result(ev_data, user_id, user_input)
                    if r:
                        results.append(r)
                return "\n".join(results) if results else None
            return self._dispatch_llm_result(cached, user_id, user_input)

        full_prompt = (
            "Eres un extractor de parámetros de agenda. "
            "Responde SOLO con JSON válido, sin markdown ni explicaciones.\n\n"
            f"{_SCHEDULE_EXTRACTION_PROMPT}\n\n"
            f"Mensaje del usuario: {user_input}"
        )

        data = await self._run_primary_extraction(full_prompt)
        used_fallback = False
        if not self._is_useful_extraction(data):
            logger.info(
                "Schedule extractor: primary returned %s, retrying with DeepSeek v4 flash",
                "None" if data is None else f"action={data.get('action', '?')}",
            )
            ds_data = await self._run_deepseek_fallback(full_prompt)
            if self._is_useful_extraction(ds_data):
                data = ds_data
                used_fallback = True

        if not self._is_useful_extraction(data):
            return None

        if used_fallback:
            logger.info("Schedule extractor: DeepSeek fallback succeeded for input: %r", user_input[:80])

        # Multi-event response {"action": "create", "events": [...]}
        if data.get("events") and isinstance(data["events"], list) and len(data["events"]) >= 1:
            events_with_action = [{**ev, "action": "create"} for ev in data["events"]]
            async with self._llm_cache_lock:
                self._llm_cache[cache_key] = events_with_action
                if len(self._llm_cache) > 100:
                    self._llm_cache.pop(next(iter(self._llm_cache)))
            results = []
            for ev_data in events_with_action:
                r = self._dispatch_llm_result(ev_data, user_id, user_input)
                if r:
                    results.append(r)
            # Save pattern context if all events share the same title and are on weekdays
            # so the user can follow up with "repite este patrón"
            ev_titles = [_clean_title(e.get("title") or "") for e in data["events"]]
            if len(set(t.lower() for t in ev_titles)) == 1 and ev_titles[0]:
                day_contexts = []
                now = datetime.now()
                for ev_data in data["events"]:
                    dk = (ev_data.get("date_keyword") or "").lower()
                    sh = ev_data.get("start_hour") or 0
                    sm = ev_data.get("start_minute") or 0
                    eh = ev_data.get("end_hour") or (sh + 1)
                    em = ev_data.get("end_minute") or 0
                    wd_num = _WEEKDAYS.get(dk)
                    abbrev = _WEEKDAY_TO_ABBREV.get(dk)
                    if wd_num is not None and abbrev:
                        delta = (wd_num - now.weekday()) % 7
                        anchor = now + timedelta(days=delta)
                        adj_start = anchor.replace(hour=sh, minute=sm, second=0, microsecond=0)
                        adj_end = anchor.replace(hour=eh, minute=em, second=0, microsecond=0)
                        if adj_end <= adj_start:
                            adj_end += timedelta(days=1)
                        day_contexts.append({
                            "abbrev": abbrev,
                            "start_ts": _to_utc_ts(adj_start),
                            "end_ts": _to_utc_ts(adj_end),
                        })
                if day_contexts:
                    self._last_created_series[user_id] = {
                        "title": ev_titles[0],
                        "category": data["events"][0].get("category", "other"),
                        "days": day_contexts,
                    }
            return "\n".join(results) if results else None

        async with self._llm_cache_lock:
            self._llm_cache[cache_key] = dict(data)
            if len(self._llm_cache) > 100:
                self._llm_cache.pop(next(iter(self._llm_cache)))
        return self._dispatch_llm_result(data, user_id, user_input)

    async def _classify_university_event(self, title: str, user_input: str) -> str:
        """Usa Haiku para distinguir university / uni_exam / uni_assignment cuando las keywords no son suficientes."""
        cache_key = f"uni_cat:{title.lower()[:50]}"
        if cache_key in self._llm_cache and isinstance(self._llm_cache[cache_key], str):
            return self._llm_cache[cache_key]

        llm_manager = None
        try:
            import web_app as _wa
            orch = _wa.app.state.orchestrator if hasattr(_wa, 'app') else None
            if orch is not None:
                llm_manager = orch.llm
        except Exception:
            pass

        if llm_manager is None or not hasattr(llm_manager, 'generate'):
            return "university"

        try:
            prompt = _UNI_CLASSIFY_PROMPT.format(
                title=title,
                input=user_input[:200],
            )
            full = "Responde SOLO con una de: university, uni_exam, uni_assignment\n\n" + prompt
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: llm_manager.generate(
                        full,
                        force_cloud=True,
                        force_local=False,
                    )
                ),
                timeout=8.0,
            )
            result = raw.strip().lower()
            for valid in ("uni_exam", "uni_assignment", "university"):
                if valid in result:
                    async with self._llm_cache_lock:
                        self._llm_cache[cache_key] = valid
                        if len(self._llm_cache) > 100:
                            self._llm_cache.pop(next(iter(self._llm_cache)))
                    return valid
        except asyncio.TimeoutError:
            logger.warning("University classifier LLM timed out for title: %r", title[:60])
        except Exception as exc:
            logger.debug("University classifier LLM error: %s", exc)

        return "university"

    def _dispatch_llm_result(self, data: dict, user_id: str, user_input: str = "") -> Optional[str]:
        """Convierte el resultado del LLM en una acción de agenda concreta."""
        action = data.get("action", "none")
        title = _clean_title(data.get("title") or "")
        date_kw = data.get("date_keyword")  # "hoy", "mañana", "lunes", etc. or None
        start_h = data.get("start_hour")
        start_m = data.get("start_minute", 0)
        end_h = data.get("end_hour")
        end_m = data.get("end_minute", 0)
        category = data.get("category", "other")
        event_id = data.get("event_id")
        notes = data.get("notes") or ""

        if action == "list":
            return self._handle_list(user_id, date_kw or "próximos")

        if action == "delete":
            svc = self._get_service()
            if event_id:
                ev = svc.get_event_by_id(event_id) if hasattr(svc, 'get_event_by_id') else None
                if ev and svc.delete_event(event_id):
                    return f"🗑️ Evento #{event_id} eliminado: '{ev.title}'. Si fue un error, volvé a agendarlo."
                return f"No encontré el evento #{event_id}."
            if title:
                # Buscar por título
                from datetime import datetime as _dt, time as _dtime, timedelta as _td
                now = _dt.now()
                start = _dt.combine(now.date(), _dtime.min)
                end = start + _td(days=7)
                events = svc.get_events_in_range(user_id, start.timestamp(), end.timestamp())
                for ev in events:
                    if ev.title.lower().strip() == title.lower().strip():
                        svc.delete_event(ev.id)
                        return f"🗑️ Evento #{ev.id} eliminado: '{ev.title}'. Si fue un error, volvé a agendarlo."
            return "No supe cuál evento eliminar. Probá: \"elimina evento 13\"."

        if action == "modify":
            if not event_id:
                return "Necesito el número del evento. Probá: \"modifica evento 13\"."
            svc = self._get_service()
            if not hasattr(svc, 'update_event'):
                return "El sistema de agenda necesita reiniciarse para editar."
            kwargs = {}
            if title:
                kwargs["title"] = title
            if category and category != "other":
                kwargs["category"] = category
            if start_h is not None:
                from datetime import datetime as _dt, time as _dtime, timedelta as _td
                now_ts = _dt.now().timestamp()
                ev = svc.get_event_by_id(event_id)
                if ev:
                    base_date = _dt.fromtimestamp(ev.start_ts_utc)
                else:
                    base_date = _dt.now()
                start_dt = base_date.replace(hour=start_h, minute=start_m or 0)
                kwargs["start_ts_utc"] = start_dt.timestamp()
                if end_h is not None:
                    end_dt = base_date.replace(hour=end_h, minute=end_m or 0)
                    if end_dt <= start_dt:
                        end_dt += _td(days=1)
                    kwargs["end_ts_utc"] = end_dt.timestamp()
            if notes:
                kwargs["notes"] = notes
            svc.update_event(event_id, **kwargs)
            return f"✅ Evento #{event_id} actualizado."

        if action == "create":
            if not title:
                return "Me falta el título del evento. Probá incluir qué actividad es."
            if _BAD_TITLE_PATTERN.search(title):
                return (
                    "Parece que querías modificar un evento existente. "
                    "Probá: \"modifica evento N, hora inicio 7, hora fin 9 y media\"."
                )
            if start_h is None:
                return "Me falta la hora. Probá: \"... a las 8\" o \"... de 7 a 9\"."

            svc = self._get_service()
            from datetime import datetime as _dt, time as _dtime, timedelta as _td
            now = _dt.now()

            # Resolver fecha
            if date_kw == "hoy":
                target_date = _dt.combine(now.date(), _dtime.min)
            elif date_kw == "mañana":
                target_date = _dt.combine((now + _td(days=1)).date(), _dtime.min)
            elif date_kw and date_kw.lower() in _WEEKDAYS:
                wd = _WEEKDAYS[date_kw.lower()]
                delta = (wd - now.weekday()) % 7
                target_date = _dt.combine((now + _td(days=delta)).date(), _dtime.min)
            elif date_kw:
                explicit = _parse_explicit_date(date_kw)
                if explicit:
                    target_date = _dt.combine(explicit.date(), _dtime.min)
                else:
                    target_date = _dt.combine(now.date(), _dtime.min)
            else:
                target_date = _dt.combine(now.date(), _dtime.min)

            start_dt = target_date.replace(hour=start_h, minute=start_m or 0)
            if start_dt <= now and date_kw in (None, "hoy"):
                start_dt += _td(days=1)

            if end_h is not None:
                end_dt = target_date.replace(hour=end_h, minute=end_m or 0)
                if end_dt <= start_dt:
                    end_dt += _td(days=1)
            else:
                end_dt = start_dt + _td(hours=1)

            if category not in {"university", "uni_exam", "uni_assignment", "course_study", "personal_project", "client_project", "exercise", "other"}:
                category = _infer_category(title.lower())
            # Fallback al input completo si el título no tiene la keyword de categoría
            if category == "other" and user_input:
                category = _infer_category(user_input.lower())

            event = svc.create_event(
                user_id=user_id,
                title=title,
                category=category,
                start_ts_utc=_to_utc_ts(start_dt),
                end_ts_utc=_to_utc_ts(end_dt),
                notes=notes,
            )
            local_range = f"{start_dt.strftime('%d/%m %H:%M')}–{end_dt.strftime('%H:%M')}"
            msg = (
                f"✅ Evento #{event.id} agendado: '{event.title}' "
                f"[{event.category}] · {local_range}"
            )
            end_hm = (end_h, end_m) if end_h is not None else None
            msg += _ambiguity_note(title, category, end_hm, event.id)
            return msg

        return None

    # ── Handle ────────────────────────────────────────────────────

    async def handle_forced(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        """Dispatch forzado desde el classifier cloud (Fase 2). handle() arranca
        con el guard ``if not matches_command: return None``, que rechaza formas
        raras ("fijate qué tengo encima esta semana"). Acá salteamos ese guard y
        entramos directo al extractor LLM, que reusa todo el parsing/dispatch
        (create/list/modify/delete) ya existente."""
        # PERO mantenemos los guards DUROS de matches_command: aunque el classifier
        # cloud nos haya forzado, hay queries que NO son nuestras. "pendientes en
        # Canvas" pertenece a Canvas; sin esto el extractor toma "canvas" como
        # título de curso → "No encontré un curso llamado 'canvas.'". Vision Q&A
        # tiene su propia prioridad.
        lower = normalize_voseo(user_input.lower().strip())
        # Dominio Canvas (literal "canvas" o notas/calificaciones/pendientes/entregas):
        # no es nuestro. Sin esto, el forced dispatch tomaba "pendientes en canvas" y el
        # extractor lo mangeaba como "curso llamado canvas".
        if "canvas" in lower or _CANVAS_NOTE_HINT.search(lower):
            return None
        try:
            from skills.builtin.vision_query.skill import _VISION_QUERY_RE
            if _VISION_QUERY_RE.search(lower):
                return None
        except ImportError:
            pass
        user_id = ctx.user_role or "admin"
        return await self._extract_schedule_intent(normalize_voseo(user_input), user_id)

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input, ctx):
            return None

        # Normalizamos voseo→tuteo para que el parsing (verbos semánticos,
        # extracción de título) vea "agenda"/"pon" como ya espera.
        user_input = normalize_voseo(user_input)
        lower = user_input.lower().strip()
        user_id = ctx.user_role or "admin"

        # Pomodoro config response has priority — must be checked before _matches_pomodoro
        # so that "descanso corto" in the config answer doesn't match _POMODORO_BREAK_RE
        if self._pending_pomodoro_setup.get(user_id):
            return await self._pomodoro_parse_config(user_input, ctx)

        # Pending extend confirmation: user previously asked to add minutes but the
        # heuristic punted ("¿este ciclo o uno nuevo?"). Capture their answer here.
        pending_min = self._pending_pomodoro_extend.get(user_id)
        if pending_min:
            from modules.schedule.monitor import get_focus_monitor
            monitor = get_focus_monitor()
            if _POMODORO_EXTEND_CONFIRM_NEW_RE.search(lower):
                self._pending_pomodoro_extend.pop(user_id, None)
                return await monitor.extend_pomodoro(0, mode="new_cycle")
            if _POMODORO_EXTEND_CONFIRM_CURRENT_RE.search(lower):
                self._pending_pomodoro_extend.pop(user_id, None)
                return await monitor.extend_pomodoro(pending_min, mode="current")
            # Anything else clears the pending state and falls through to normal routing.
            self._pending_pomodoro_extend.pop(user_id, None)

        if self._matches_pomodoro(lower):
            return await self._handle_pomodoro(lower, ctx)

        if self._matches_focus_control(lower):
            return await self._handle_focus_control(lower, ctx)

        # Si menciona "evento N" sin verbo de creación, es modify/delete implícito.
        # Esto cubre hesitaciones de voz ("el evento 15, no, empieza a las 7...")
        # y verbos de consulta no listados en _MODIFY_PATTERN.
        if _EVENT_REF_PATTERN.search(lower) and not _CREATE_VERB_PATTERN.search(lower):
            if _DELETE_PATTERN.search(lower):
                return self._handle_delete_via_voice(user_input, user_id)
            return self._handle_update(user_input, user_id)

        # "hoy/mañana/esta noche tengo X" sin verbo de creación → consultar agenda.
        # PERO si hay rango horario explícito ("tengo X de 7 a 9"), es un DICTADO
        # de creación, no una consulta → dejar pasar a create / extractor LLM.
        if (_DAY_TENGO_QUERY_RE.search(lower)
                and not _CREATE_VERB_PATTERN.search(lower)
                and not _has_time_range(lower)):
            return self._handle_list(user_id, lower, ctx)

        # "a qué hora termina/empieza ese curso" → buscar en historial y mostrar detalle
        if _SCHEDULE_QUERY_RE.search(lower) and not _CREATE_VERB_PATTERN.search(lower):
            last_event = self._extract_last_event_from_history(ctx)
            if last_event:
                return self._handle_event_detail(last_event, user_id, ctx)
            return self._handle_list(user_id, "hoy", ctx)

        # "repite este patrón" / "todo el mes" → expand stored pattern as recurring series
        if _REPEAT_PATTERN_RE.search(lower):
            return await self._handle_repeat_pattern(user_id)

        # Si los patrones regex no matchean pero sí el LLM hint,
        # intentar extracción vía Haiku antes de caer al fallback
        if not self._matches_semantic(lower):
            llm_result = await self._extract_schedule_intent(user_input, user_id)
            if llm_result is not None:
                return llm_result

        # Delete y modify se chequean antes que create
        if _DELETE_PATTERN.search(lower):
            return self._handle_delete_via_voice(user_input, user_id)

        if _RESCHEDULE_PATTERN.search(lower):
            return self._handle_reschedule(user_input, user_id)

        if _MODIFY_PATTERN.search(lower):
            return self._handle_update(user_input, user_id)

        # Listings — detectar frases de consulta (no creación)
        if lower in ("/hoy", "/agenda", "/semana") \
           or lower.startswith("qué tengo") \
           or lower.startswith("que tengo") \
           or lower.startswith("dime que tengo") \
           or lower.startswith("dime que cursos tengo") \
           or lower.startswith("revisa mi agenda") \
           or lower.startswith("revisa la agenda") \
           or lower.startswith("muéstrame mi agenda") \
           or lower.startswith("dime mi agenda") \
           or lower.startswith("qué cursos tengo") \
           or lower.startswith("que cursos tengo") \
           or lower.startswith("qué hay") \
           or lower.startswith("que hay") \
           or lower.startswith("muéstrame la agenda") \
           or "que tengo" in lower \
           or ("revisa" in lower and "agenda" in lower) \
           or _LIST_AGENDA_RE.search(lower):
            return self._handle_list(user_id, lower, ctx)

        return await self._handle_create(user_input, user_id)

    # ── Create ────────────────────────────────────────────────────

    async def _handle_create(self, user_input: str, user_id: str) -> str:
        try:
            service = self._get_service()
        except Exception as exc:
            return f"Sistema de agenda no disponible: {exc}"

        lower = user_input.lower()

        # Categoría explícita ("categoría universidad")
        category = "other"
        cat_match = re.search(r"\bcategor[ií]a\s+(\w+)", lower)
        if cat_match:
            raw = cat_match.group(1)
            category = _CATEGORY_ALIASES.get(raw, raw)

        # Parsing temporal: día + hora(s)
        target_date = _parse_target_date(lower)
        start_hm, end_hm = _parse_time_range(lower)

        if target_date is None and start_hm is None:
            return (
                "No entendí cuándo. Probá: `agenda gym hoy 18:00`, "
                "`pon clase de cálculo el lunes de 8 a 10`, o "
                "`agenda yoga mañana a las 7 y media`."
            )

        if target_date is None:
            target_date = datetime.combine(datetime.now().date(), dtime.min)

        if start_hm is None:
            return "Me falta la hora. Probá: `... a las 8` o `... de 7 a 9`."

        start_dt = target_date.replace(hour=start_hm[0], minute=start_hm[1])
        # Si el target_date es hoy y la hora ya pasó, mover a mañana
        now = datetime.now()
        if start_dt <= now and "hoy" in lower:
            start_dt += timedelta(days=1)

        if end_hm is not None:
            end_dt = target_date.replace(hour=end_hm[0], minute=end_hm[1])
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
        else:
            end_dt = start_dt + timedelta(hours=1)

        # Título: limpia el texto original
        title = _extract_title(user_input)
        if not title or _is_title_suspicious(title):
            # Regex extraction failed or looks wrong — fall back to Haiku
            llm_result = await self._extract_schedule_intent(user_input, user_id)
            if llm_result is not None:
                return llm_result
            if not title:
                return "Falta el título del evento. Probá incluir qué actividad es."

        if category == "other":
            category = _infer_category(title.lower())
        # Si el título no tiene suficiente contexto, buscar en el input completo
        if category == "other":
            category = _infer_category(lower)
        # Haiku classifier para eventos universitarios ambiguos
        if category == "other" and _UNI_SIGNAL_RE.search(lower):
            category = await self._classify_university_event(title, user_input)

        recurrences = _parse_recurrences(lower)

        if recurrences:
            all_created = []
            day_contexts = []
            for rec in recurrences:
                if rec.startswith("weekly:") and rec != "weekly":
                    abbrev = rec.split(":", 1)[1]
                    wd_num = _ABBREV_TO_WD_NUM.get(abbrev, 0)
                    delta = (wd_num - start_dt.weekday()) % 7
                    adj_start = start_dt + timedelta(days=delta)
                    adj_end = end_dt + timedelta(days=delta)
                else:
                    abbrev = ""
                    adj_start, adj_end = start_dt, end_dt
                series = service.create_recurring_series(
                    user_id=user_id,
                    title=title,
                    category=category,
                    start_ts_utc=_to_utc_ts(adj_start),
                    end_ts_utc=_to_utc_ts(adj_end),
                    recurrence=rec,
                    weeks=16,
                )
                all_created.extend(series)
                day_contexts.append({
                    "abbrev": abbrev or "ALL",
                    "start_ts": _to_utc_ts(adj_start),
                    "end_ts": _to_utc_ts(adj_end),
                })
            self._last_created_series[user_id] = {
                "title": title,
                "category": category,
                "days": day_contexts,
            }
            local_range = f"{start_dt.strftime('%H:%M')}–{end_dt.strftime('%H:%M')}"
            if len(recurrences) == 1:
                return (
                    f"✅ Serie recurrente creada: '{title}' [{category}] "
                    f"· {local_range} · {len(all_created)} sesiones (parent #{all_created[0].id})"
                )
            days_str = ", ".join(d["abbrev"] for d in day_contexts)
            return (
                f"✅ Series recurrentes creadas: '{title}' [{category}] "
                f"· {local_range} · {len(all_created)} sesiones totales ({days_str})"
            )

        event = service.create_event(
            user_id=user_id,
            title=title,
            category=category,
            start_ts_utc=_to_utc_ts(start_dt),
            end_ts_utc=_to_utc_ts(end_dt),
        )
        local_range = (
            f"{start_dt.strftime('%d/%m %H:%M')}–{end_dt.strftime('%H:%M')}"
        )
        msg = (
            f"✅ Evento #{event.id} agendado: '{event.title}' "
            f"[{event.category}] · {local_range}"
        )
        msg += _ambiguity_note(title, category, end_hm, event.id)
        return msg

    # ── Update (voz) ───────────────────────────────────────────────

    def _handle_update(self, user_input: str, user_id: str) -> str:
        try:
            service = self._get_service()
        except Exception as exc:
            return f"Sistema de agenda no disponible: {exc}"

        lower = user_input.lower()

        # Extraer ID del evento: "evento 13", "evento #13", "evento número 13"
        m = re.search(r"\bevento\s*(?:(?:n[úu]mero|n[úu]m\.?|nro\.?|#)\s*)?(\d+)", lower)
        if not m:
            return (
                "No encontré el número de evento. Probá: "
                "\"modifica evento 13 para que termine a las 11:10\"."
            )
        event_id = int(m.group(1))

        # Quitar la mención del evento ID del texto antes de parsear hora/fecha,
        # si no _parse_time_range capturaría el "13" de "evento 13" como hora suelta.
        # En lockstep con el regex de extracción, o "número" quedaría suelto.
        lower_clean = re.sub(r"\bevento\s*(?:(?:n[úu]mero|n[úu]m\.?|nro\.?|#)\s*)?\d+", "", lower)

        if not hasattr(service, 'get_event_by_id'):
            return (
                "El sistema de agenda necesita reiniciarse para usar edición por voz. "
                "Usá la ventana de AGENDA (✎ botón editar) mientras tanto."
            )

        event = service.get_event_by_id(event_id)
        if event is None:
            return f"No encontré el evento #{event_id}. Usá /agenda para ver la lista."

        # Campos a actualizar
        title = None
        category = None
        start_ts = None
        end_ts = None
        notes = None

        # Nuevo título: "cambia el nombre a X", "ponle 'X'", "título X", etc.
        m_title = re.search(
            r"(?:"
            r"(?:cambia(?:\s+el)?\s+nombre(?:\s+a)?)\s+"
            r"|el\s+nombre(?:\s+(?:es|va\s+a\s+ser|sera))?\s+"
            r"|(?:ponle?|ll[áa]malo|ren[óo]mbralo)\s+(?:como\s+)?"
            r"|t[ií]tulo\s+(?:a\s+|es\s+)?"
            r")"
            r"(?:['\"](.+?)['\"]|(.+?)(?=\s*,|\s*$))",
            lower,
        )
        if m_title:
            title = (m_title.group(1) or m_title.group(2) or "").strip()
            title = title.strip("'\"").strip() or None

        # Nueva categoría: "categoría X", "cambia categoría a X"
        m_cat = re.search(r"\bcategor[ií]a\s+(?:a\s+)?(\w+)", lower)
        if m_cat:
            raw_cat = m_cat.group(1)
            category = _CATEGORY_ALIASES.get(raw_cat, raw_cat)

        # Nuevas notas: "notas X", "agrega notas X"
        m_notes = re.search(r"\bnotas?\s+(.+?)(?:\s*$)", lower)
        if m_notes:
            notes = m_notes.group(1).strip()

        # Nuevo rango horario (usar lower_clean para no confundir el ID con una hora)
        target_date = _parse_target_date(lower_clean)
        start_hm, end_hm = _parse_time_range(lower_clean)

        if target_date or start_hm:
            # Si hay información de fecha, actualizar desde el evento actual
            base_dt = datetime.fromtimestamp(
                event.start_ts_utc, tz=timezone.utc
            )
            if target_date:
                base_dt = target_date
            else:
                base_dt = datetime.combine(base_dt.date(), dtime.min)

            if start_hm:
                new_start = base_dt.replace(hour=start_hm[0], minute=start_hm[1])
                start_ts = _to_utc_ts(new_start)

            if end_hm:
                new_end = base_dt.replace(hour=end_hm[0], minute=end_hm[1])
                if start_hm and new_end <= base_dt.replace(hour=start_hm[0], minute=start_hm[1]):
                    new_end += timedelta(days=1)
                end_ts = _to_utc_ts(new_end)
            elif start_hm:
                # Si solo dan hora de inicio, asumir +1h
                end_ts = start_ts + 3600

        # Guard: si no se extrajo nada, informar antes de llamar al servicio
        if not any([title, category, start_ts, end_ts, notes]):
            return (
                f"No detecté qué cambiar del evento #{event_id}. "
                f"Probá: \"modifica evento {event_id}, hora fin 11:10\" o "
                f"\"cambia el nombre del evento {event_id} a Nuevo título\"."
            )

        # Aplicar actualización
        updated = service.update_event(
            event_id=event_id,
            title=title,
            category=category,
            start_ts_utc=start_ts,
            end_ts_utc=end_ts,
            notes=notes,
        )

        if updated is None:
            return f"No se pudo actualizar el evento #{event_id}."

        # Construir mensaje de confirmación
        changes = []
        if title is not None:
            changes.append(f"título → '{title}'")
        if category is not None:
            changes.append(f"categoría → {category}")
        if start_ts is not None:
            s = datetime.fromtimestamp(start_ts)
            changes.append(f"inicio → {s.strftime('%H:%M')}")
        if end_ts is not None:
            e = datetime.fromtimestamp(end_ts)
            changes.append(f"fin → {e.strftime('%H:%M')}")
        if notes is not None:
            changes.append("notas actualizadas")

        if not changes:
            return (
                f"No detecté qué cambiar del evento #{event_id}. "
                f"Probá: \"modifica evento {event_id}, hora fin 11:10\""
            )

        return f"✅ Evento #{event_id} actualizado: {', '.join(changes)}."

    # ── Reschedule (voz) ────────────────────────────────────────────

    def _handle_reschedule(self, user_input: str, user_id: str) -> str:
        """Reprograma un evento a un nuevo horario basado en slots libres."""
        try:
            service = self._get_service()
        except Exception as exc:
            return f"Sistema de agenda no disponible: {exc}"

        lower = user_input.lower()

        # Detectar categoría o título mencionado
        target_cat = None
        target_title = None
        for cat_key, cat_val in _CATEGORY_ALIASES.items():
            if cat_key in lower:
                target_cat = cat_val
                break

        # Buscar eventos pending para hoy
        from datetime import datetime as _dt, time as _dtime, timedelta as _td
        now = _dt.now()
        day_start = _dt.combine(now.date(), _dtime.min)
        day_end = day_start + _td(days=1)
        today_events = service.get_events_in_range(
            user_id, day_start.timestamp(), day_end.timestamp()
        )
        candidates = [e for e in today_events if e.status == "pending"]

        # Filtrar por categoría o keyword en título
        if target_cat:
            candidates = [e for e in candidates if e.category == target_cat]
        else:
            # Buscar por palabras clave en el título
            title_keywords = re.findall(r'\b\w{4,}\b', lower)
            for kw in title_keywords:
                matched = [e for e in candidates if kw.lower() in e.title.lower()]
                if matched:
                    candidates = matched
                    break

        if not candidates:
            return (
                "No encontré eventos pendientes para hoy. "
                "Decime el número de evento: \"reprográmame el evento 14\"."
            )

        if len(candidates) == 1:
            event = candidates[0]
        else:
            lines = ["¿Cuál de estos eventos querés reprogramar?"]
            for ev in candidates:
                t = _dt.fromtimestamp(ev.start_ts_utc)
                lines.append(f"  • #{ev.id} — {ev.title} ({t.strftime('%H:%M')}) [{ev.category}]")
            return "\n".join(lines)

        # Buscar slots libres mañana (default)
        tomorrow = (now + _td(days=1)).strftime("%Y-%m-%d")
        duration = int((event.end_ts_utc - event.start_ts_utc) / 60)
        slots = service.find_free_slots(user_id, tomorrow, duration_minutes=duration)

        if not slots:
            # Intentar pasado mañana
            day_after = (now + _td(days=2)).strftime("%Y-%m-%d")
            slots = service.find_free_slots(user_id, day_after, duration_minutes=duration)
            if not slots:
                return (
                    f"No encontré slots libres de {duration} min en los próximos 2 días. "
                    f"Probá diciéndome un día específico."
                )

        # Formatear slots para el usuario elegir
        slot_lines = []
        from datetime import timezone as _tz
        for i, s in enumerate(slots[:5]):
            st = _dt.fromtimestamp(s["start"], tz=_tz.utc)
            en = _dt.fromtimestamp(s["end"], tz=_tz.utc)
            slot_lines.append(f"  {i+1}. {st.strftime('%H:%M')}–{en.strftime('%H:%M')}")

        date_label = "mañana" if tomorrow == (now + _td(days=1)).strftime("%Y-%m-%d") else "pasado mañana"
        return (
            f"📅 Para {date_label}, tenés estos slots libres de {duration} min:\n"
            + "\n".join(slot_lines)
            + f"\n\nDecime el número (1-{len(slots[:5])}) o una hora específica."
        )

    # ── Pomodoro ──────────────────────────────────────────────────

    async def _handle_repeat_pattern(self, user_id: str) -> str:
        """Expands the last created event pattern as weekly recurring series."""
        ctx = self._last_created_series.get(user_id)
        if not ctx:
            return (
                "No encontré un patrón reciente para repetir. "
                "Primero creá los eventos de la semana y después pedime que los repita."
            )
        try:
            svc = self._get_service()
        except Exception as exc:
            return f"Sistema de agenda no disponible: {exc}"

        title = ctx["title"]
        category = ctx["category"]
        results = []
        for day in ctx["days"]:
            abbrev = day["abbrev"]
            if abbrev == "ALL":
                continue
            series = svc.create_recurring_series(
                user_id=user_id,
                title=title,
                category=category,
                start_ts_utc=day["start_ts"],
                end_ts_utc=day["end_ts"],
                recurrence=f"weekly:{abbrev}",
                weeks=16,
            )
            results.append(f"{abbrev}: {len(series)} sesiones (parent #{series[0].id})")

        if not results:
            return "El patrón guardado no tiene días de semana para repetir."

        self._last_created_series.pop(user_id, None)
        return (
            f"✅ Patrón repetido para '{title}' [{category}]: "
            + " | ".join(results)
        )

    async def _handle_focus_control(self, lower: str, ctx: RequestContext) -> str:
        """Despacha comandos de modo estudio, whitelist y reporte de distracciones."""
        from modules.schedule.monitor import get_focus_monitor
        monitor = get_focus_monitor()

        if _STUDY_MODE_START_RE.search(lower):
            return await monitor.start_study_mode()

        if _STUDY_MODE_STOP_RE.search(lower):
            return await monitor.stop_study_mode()

        if _WHITELIST_SHOW_RE.search(lower):
            return monitor.show_whitelist()

        if _FOCUS_REPORT_RE.search(lower):
            return monitor.get_distraction_report()

        m = _WHITELIST_ADD_RE.search(lower)
        if m:
            app_name = m.group(1).strip()
            if _WHITELIST_PERMANENT_RE.search(lower):
                return monitor.add_to_permanent_whitelist(app_name)
            return monitor.add_to_session_whitelist(app_name)

        return "No entendí el comando de foco. Podés decir: 'modo estudio', 'muéstrame la whitelist', 'agrega X a la whitelist'."

    async def _handle_pomodoro(self, lower: str, ctx: RequestContext) -> str:
        # ── In-session controls — checked first since they're more specific ──
        if _POMODORO_PAUSE_RE.search(lower):
            from modules.schedule.monitor import get_focus_monitor
            return await get_focus_monitor().pause_pomodoro()

        if _POMODORO_RESUME_RE.search(lower):
            from modules.schedule.monitor import get_focus_monitor
            return await get_focus_monitor().resume_pomodoro()

        if _POMODORO_SKIP_BREAK_RE.search(lower):
            from modules.schedule.monitor import get_focus_monitor
            return await get_focus_monitor().skip_break()

        if _POMODORO_EXTEND_CYCLE_RE.search(lower):
            from modules.schedule.monitor import get_focus_monitor
            return await get_focus_monitor().extend_pomodoro(0, mode="new_cycle")

        if (m := _POMODORO_EXTEND_RE.search(lower)):
            from modules.schedule.monitor import get_focus_monitor
            monitor = get_focus_monitor()
            minutes = int(m.group(1))
            if not monitor._pomodoro_task or monitor._pomodoro_task.done():
                return "No hay Pomodoro activo para extender."
            remaining = monitor._pomodoro_remaining_min()
            # Heurística: si quedan <5 min del ciclo, extender directo. Si la
            # cantidad pedida es comparable a un ciclo completo (≥25 min), asumir
            # ciclo nuevo. En la zona intermedia, confirmar para no decidir mal.
            if remaining < 5 or minutes >= 25:
                return await monitor.extend_pomodoro(minutes, mode="current")
            user_id = ctx.user_role or "admin"
            self._pending_pomodoro_extend[user_id] = minutes
            return (
                f"Te quedan {remaining} min del ciclo. "
                f"¿Extender este ciclo {minutes} min más, o agregar un ciclo nuevo al final? "
                f"Decí 'extiende' o 'ciclo nuevo'."
            )

        if _POMODORO_STOP_RE.search(lower):
            return await self._pomodoro_stop()

        if _POMODORO_STATUS_RE.search(lower):
            return self._pomodoro_status()

        if _POMODORO_BREAK_RE.search(lower):
            return self._pomodoro_break(lower)

        if _POMODORO_START_RE.search(lower) or _POMODORO_MODE_RE.search(lower):
            return await self._pomodoro_activate(lower, ctx)

        # Fallback: si solo matcheó _POMODORO_HINT_RE (mención de "pomodoro" sin
        # verbo claro), usar DeepSeek flash para extraer la acción real e inferir
        # qué quiere el usuario: start/stop/status/config.
        if _POMODORO_HINT_RE.search(lower):
            intent = await self._extract_pomodoro_intent_llm(lower)
            action = (intent.get("action") or "").lower()
            if action == "stop":
                return await self._pomodoro_stop()
            if action == "status":
                return self._pomodoro_status()
            if action in ("start", "activate", "config", "modify"):
                # Si DeepSeek extrajo config inline (work_min/cycles/break_min/task),
                # arrancar directamente. Si no, entrar al setup flow.
                work_min = intent.get("work_min")
                cycles = intent.get("cycles")
                break_min = intent.get("break_min")
                task = intent.get("task") or ""
                if any(v is not None for v in (work_min, cycles, break_min)) or task:
                    return await self._pomodoro_start_with_config(
                        task=task, work_min=work_min, cycles=cycles,
                        break_min=break_min, ctx=ctx,
                    )
                return await self._pomodoro_activate(lower, ctx)
            # action="none" o desconocido → fallback al activate (más seguro)
            return await self._pomodoro_activate(lower, ctx)

        return "No entendí el comando Pomodoro. Probá: 'activa pomodoro' o 'inicia pomodoro de 45 min para cálculo'."

    async def _extract_pomodoro_intent_llm(self, user_input: str) -> dict:
        """DeepSeek v4 flash extractor: dado un input con 'pomodoro' sin estructura
        clara, determinar la acción y extraer config inline si la hay.

        Devuelve dict: {action, work_min, cycles, break_min, task}.
        action ∈ {start, stop, status, config, modify, none}.
        """
        try:
            from core.llm_helpers import call_deepseek_flash_json
            prompt = (
                f'El usuario habló sobre Pomodoro: "{user_input}"\n'
                'Determina qué quiere hacer y extrae configuración si la menciona.\n'
                'Acciones válidas:\n'
                '  - "start": quiere iniciar una sesión nueva\n'
                '  - "stop": quiere detener/cancelar la sesión activa\n'
                '  - "status": pregunta cómo va / cuánto falta\n'
                '  - "config": está dando config (minutos, ciclos, descanso, tarea)\n'
                '  - "modify": quiere cambiar config de una sesión\n'
                '  - "none": no es claro o no tiene que ver con Pomodoro\n'
                'Responde SOLO JSON sin markdown:\n'
                '{"action": string, "work_min": int_or_null, "cycles": int_or_null, '
                '"break_min": int_or_null, "task": string_or_null}'
            )
            raw = await call_deepseek_flash_json(prompt, timeout=8.0, max_tokens=256)
            if raw:
                parsed = _parse_schedule_llm_json(raw)
                if parsed:
                    return parsed
        except Exception as exc:
            logger.debug("[pomodoro] LLM intent extraction failed: %s", exc)
        return {"action": "start"}  # fallback seguro: tratar como start

    async def _pomodoro_activate(self, lower: str, ctx: RequestContext) -> str:
        """Entry point for activation: ask for config if not provided inline."""
        from modules.schedule.monitor import get_focus_monitor
        monitor = get_focus_monitor()

        if monitor._pomodoro_task and not monitor._pomodoro_task.done():
            remaining = monitor._pomodoro_remaining_min()
            return f"Ya hay un Pomodoro activo. Faltan {remaining} min. Decí 'para el pomodoro' para cancelarlo."

        m = _POMODORO_START_RE.search(lower)
        # Check if duration or task was provided inline
        has_inline = m and (m.group("task") or m.group(1))
        if has_inline:
            return await self._pomodoro_start_with_config(
                task=m.group("task") or "",
                work_min=int(m.group(1)) if m.group(1) else None,
                cycles=None,
                break_min=None,
                ctx=ctx,
            )

        # No inline config → ask grouped question
        user_id = ctx.user_role or "admin"
        self._pending_pomodoro_setup[user_id] = True
        return (
            "Listo para iniciar. Dime:\n"
            "  • ¿Para qué tarea o curso?\n"
            "  • ¿Cuántos ciclos? (default 4)\n"
            "  • ¿Duración de trabajo? (default 25 min)\n"
            "  • ¿Descanso corto? (default 5 min)"
        )

    async def _pomodoro_parse_config(self, user_input: str, ctx: RequestContext) -> str:
        """Parse a config response and start the Pomodoro. Has DeepSeek fallback.

        Supports multi-turn: if the user only provides a task name (no numerics),
        stores the task and asks for cycles/duration/break without clearing the flag.
        The user can then send the full config OR say 'ok' to accept defaults.
        """
        user_id = ctx.user_role or "admin"
        lower = user_input.lower()

        # Short affirmation → accept defaults (uses task stored from previous turn if any)
        _OK_RE = re.compile(
            r'^\s*(?:ok|sí|si|dale|de\s+acuerdo|sigue|arranca|por\s+defecto|default|listo|ya|claro)\s*$',
            re.IGNORECASE,
        )
        if _OK_RE.search(lower):
            task = self._pending_pomodoro_task.pop(user_id, "")
            self._pending_pomodoro_setup.pop(user_id, None)
            return await self._pomodoro_start_with_config(
                task=task, work_min=None, cycles=None, break_min=None, ctx=ctx
            )

        # Try regex extractors first
        cycles_m  = _POMO_CFG_CYCLES_RE.search(lower)
        work_m    = _POMO_CFG_WORK_RE.search(lower)
        break_m   = _POMO_CFG_BREAK_RE.search(lower)

        if cycles_m:
            raw = cycles_m.group(1)
            if raw:
                cycles = int(raw)
            else:
                # matched a word-number pattern — extract word from the full match
                word = re.search(r'(uno|dos|tres|cuatro|cinco|seis|siete|ocho)', cycles_m.group(0), re.IGNORECASE)
                cycles = _WORD_CYCLE_MAP.get(word.group(1).lower()) if word else None
        else:
            cycles = None
        work_min  = int(work_m.group(1) or work_m.group(2))   if work_m   else None
        break_min = int(break_m.group(1) or break_m.group(2)) if break_m  else None

        # Extract task: text before first digit or first comma.
        # Also recover task stored from a previous partial turn.
        task = self._pending_pomodoro_task.get(user_id, "")
        task_m = re.match(r'^([a-záéíóúüñ\s]+?)(?:\s*,|\s+\d)', lower.strip())
        if task_m:
            task = task_m.group(1).strip() or task

        # If any numeric field is missing, use DeepSeek to fill gaps.
        if work_min is None or cycles is None or break_min is None:
            cfg = await self._parse_pomodoro_config_llm(user_input)
            task = task or cfg.get("task") or ""
            if cycles is None:
                cycles = cfg.get("cycles")
            if work_min is None:
                work_min = cfg.get("work_min")
            if break_min is None:
                break_min = cfg.get("break_min")

        # If ALL numeric fields are still missing the user only sent a task name
        # (likely an accidental early Enter). Store the task and ask for the rest
        # without clearing the pending flag — avoids starting with silent defaults.
        if cycles is None and work_min is None and break_min is None:
            if task:
                self._pending_pomodoro_task[user_id] = task
                return (
                    f"Anotado: '{task}'.\n"
                    f"Ahora dime los valores (o decí 'ok' para usar los defaults):\n"
                    f"  • Ciclos (default 4)\n"
                    f"  • Duración de trabajo (default 25 min)\n"
                    f"  • Descanso corto (default 5 min)"
                )
            # Nothing at all extracted — re-prompt
            return (
                "No entendí la configuración. Probá:\n"
                "  'diseño BD, 4 ciclos, 45 min trabajo, 10 descanso'\n"
                "O decí 'ok' para usar los valores por defecto."
            )

        self._pending_pomodoro_task.pop(user_id, None)
        self._pending_pomodoro_setup.pop(user_id, None)
        return await self._pomodoro_start_with_config(
            task=task, work_min=work_min, cycles=cycles, break_min=break_min, ctx=ctx
        )

    async def _parse_pomodoro_config_llm(self, user_input: str) -> dict:
        """DeepSeek v4 flash JSON fallback for Pomodoro config extraction."""
        try:
            from core.llm_helpers import call_deepseek_flash_json
            prompt = (
                f'El usuario configuró su sesión Pomodoro con: "{user_input}"\n'
                'Extrae en JSON. Usa null si no se menciona:\n'
                '{"task": string_or_null, "cycles": int_or_null, '
                '"work_min": int_or_null, "break_min": int_or_null}'
            )
            raw = await call_deepseek_flash_json(prompt, timeout=8.0, max_tokens=256)
            if raw:
                return _parse_schedule_llm_json(raw) or {}
        except Exception:
            pass
        return {}

    async def _pomodoro_start_with_config(
        self,
        task: str,
        work_min: Optional[int],
        cycles: Optional[int],
        break_min: Optional[int],
        ctx: RequestContext,
    ) -> str:
        from modules.schedule.monitor import get_focus_monitor
        monitor = get_focus_monitor()

        work_min  = work_min  or monitor.POMODORO_WORK_MIN
        cycles    = cycles    or 4
        break_min = break_min or monitor.POMODORO_SHORT_BREAK_MIN

        return await monitor.start_pomodoro(
            task_name=task,
            duration_min=work_min,
            total_cycles=cycles,
            short_break_min=break_min,
        )

    async def _pomodoro_stop(self) -> str:
        from modules.schedule.monitor import get_focus_monitor
        monitor = get_focus_monitor()
        return await monitor.stop_pomodoro()

    def _pomodoro_status(self) -> str:
        from modules.schedule.monitor import get_focus_monitor
        monitor = get_focus_monitor()
        return monitor.status()

    def _pomodoro_break(self, lower: str) -> str:
        from modules.schedule.monitor import get_focus_monitor
        monitor = get_focus_monitor()

        if monitor._pomodoro_task and not monitor._pomodoro_task.done():
            return (
                "Hay un Pomodoro activo. "
                "Cuando termine, decime 'descanso corto' o 'descanso largo'."
            )

        if "largo" in lower:
            return f"Descanso largo: {monitor.POMODORO_LONG_BREAK_MIN} minutos. Relajate bien."
        return f"Descanso corto: {monitor.POMODORO_SHORT_BREAK_MIN} minutos. Estira las piernas."

    # ── Delete (voz) ───────────────────────────────────────────────

    def _handle_delete_via_voice(self, user_input: str, user_id: str) -> str:
        try:
            service = self._get_service()
        except Exception as exc:
            return f"Sistema de agenda no disponible: {exc}"

        lower = user_input.lower()

        # Extraer ID del evento: "evento 13", "evento #13", "evento número 13"
        m = re.search(r"\bevento\s*(?:(?:n[úu]mero|n[úu]m\.?|nro\.?|#)\s*)?(\d+)", lower)
        if m:
            event_id = int(m.group(1))
            if not hasattr(service, 'get_event_by_id'):
                return (
                    "El sistema de agenda necesita reiniciarse. "
                    "Usá /agenda para ver eventos y eliminalos manualmente."
                )
            event = service.get_event_by_id(event_id)
            if event is None:
                return f"No encontré el evento #{event_id}."
            service.delete_event(event_id)
            return (
                f"🗑️ Evento #{event_id} eliminado: '{event.title}'. "
                f"Si fue un error, volvé a agendarlo."
            )

        # Sin ID: buscar por título en eventos de la semana
        now = datetime.now()
        start = datetime.combine(now.date(), dtime.min)
        end = start + timedelta(days=7)
        events = service.get_events_in_range(
            user_id=user_id,
            from_ts_utc=_to_utc_ts(start),
            to_ts_utc=_to_utc_ts(end),
        )

        # Intentar matchear título
        for ev in events:
            title_lower = ev.title.lower()
            if title_lower and len(title_lower) > 3 and title_lower in lower:
                service.delete_event(ev.id)
                return (
                    f"🗑️ Evento #{ev.id} eliminado: '{ev.title}'. "
                    f"Si fue un error, volvé a agendarlo."
                )

        return (
            "No supe cuál evento eliminar. Probá: "
            "\"elimina evento 13\" o decime el número que ves en /agenda."
        )

    # ── Cross-turn context helpers ────────────────────────────────

    def _extract_last_event_from_history(self, ctx) -> Optional[str]:
        """Busca el título del último evento mencionado en el historial conversacional."""
        history = getattr(ctx, "conversation_history", [])
        for role, content in reversed(history):
            if role == "assistant":
                matches = re.findall(r'\d{2}:\d{2} [–—] ([^\[\n]+)', content)
                if matches:
                    return matches[-1].strip()
        return None

    def _handle_event_detail(self, event_title: str, user_id: str, ctx=None) -> str:
        """Retorna detalles de un evento específico buscado por título."""
        try:
            service = self._get_service()
        except Exception as exc:
            return f"Sistema de agenda no disponible: {exc}"

        now = datetime.now()
        start = datetime.combine(now.date() - timedelta(days=1), dtime.min)
        end = start + timedelta(days=8)
        events = service.get_events_in_range(
            user_id=user_id,
            from_ts_utc=_to_utc_ts(start),
            to_ts_utc=_to_utc_ts(end),
        )

        title_key = event_title.lower()[:15]
        matching = [ev for ev in events if title_key in ev.title.lower()]
        if not matching:
            return self._handle_list(user_id, "hoy", ctx)

        ev = matching[0]
        start_dt = datetime.fromtimestamp(ev.start_ts_utc)
        end_dt = datetime.fromtimestamp(ev.end_ts_utc) if getattr(ev, "end_ts_utc", None) else None

        if ctx and getattr(ctx, "speak", False):
            time_str = _hour_to_natural(start_dt.hour, start_dt.minute)
            if end_dt and ev.end_ts_utc != ev.start_ts_utc:
                end_str = _hour_to_natural(end_dt.hour, end_dt.minute)
                return f"{ev.title} empieza a {time_str} y termina a {end_str}."
            return f"{ev.title} empieza a {time_str}. No tiene hora de fin registrada."
        else:
            time_info = start_dt.strftime('%H:%M')
            if end_dt and ev.end_ts_utc != ev.start_ts_utc:
                time_info += f" – {end_dt.strftime('%H:%M')}"
            return f"📅 {ev.title}: {time_info} [{ev.category}]"

    # ── List ──────────────────────────────────────────────────────

    def _format_list_for_voice(self, events: list, lower: str, period: "tuple | None") -> str:
        n = len(events)
        count_str = "un evento" if n == 1 else f"{n} eventos"

        if "hoy" in lower:
            day_str = "para hoy"
        elif "mañana" in lower or "manana" in lower:
            day_str = "para mañana"
        else:
            day_str = "próximamente"

        if period:
            period_names = {(18, 24): "en la noche", (12, 18): "en la tarde", (0, 12): "en la mañana"}
            day_str += f" {period_names.get(period, '')}"

        parts = []
        for ev in events:
            start_dt = datetime.fromtimestamp(ev.start_ts_utc)
            end_dt = datetime.fromtimestamp(ev.end_ts_utc) if getattr(ev, "end_ts_utc", None) else None
            time_str = _hour_to_natural(start_dt.hour, start_dt.minute)
            if end_dt and ev.end_ts_utc != ev.start_ts_utc:
                end_str = _hour_to_natural(end_dt.hour, end_dt.minute)
                parts.append(f"{ev.title}, de {time_str} a {end_str}")
            else:
                parts.append(f"{ev.title} a {time_str}")

        if n == 1:
            return f"Sí señor, tiene {count_str} {day_str}: {parts[0]}."
        items = ", ".join(parts[:-1]) + f" y {parts[-1]}"
        return f"Sí señor, tiene {count_str} {day_str}: {items}."

    def _handle_list(self, user_id: str, lower: str, ctx=None) -> str:
        try:
            service = self._get_service()
        except Exception as exc:
            return f"Sistema de agenda no disponible: {exc}"

        now = datetime.now()
        if lower == "/hoy" or "hoy" in lower:
            start = datetime.combine(now.date(), dtime.min)
            end = start + timedelta(days=1)
            label = "Hoy"
        elif "mañana" in lower or "manana" in lower:
            start = datetime.combine(now.date() + timedelta(days=1), dtime.min)
            end = start + timedelta(days=1)
            label = "Mañana"
        else:
            start = datetime.combine(now.date(), dtime.min)
            end = start + timedelta(days=7)
            label = "Próximos 7 días"

        events = service.get_events_in_range(
            user_id=user_id,
            from_ts_utc=_to_utc_ts(start),
            to_ts_utc=_to_utc_ts(end),
        )

        # Filtrar por período del día si el usuario menciona "noche", "tarde", etc.
        period = _period_range(lower)
        if period and label == "Hoy":
            sh, eh = period
            events = [
                ev for ev in events
                if sh <= datetime.fromtimestamp(ev.start_ts_utc).hour < eh
            ]

        # Para "hoy" sin período específico, ocultar eventos que ya terminaron
        if label == "Hoy" and not period:
            upcoming = [ev for ev in events if ev.start_ts_utc >= now.timestamp()]
            if upcoming:
                events = upcoming

        if not events:
            if period:
                period_label = {(18, 24): "noche", (12, 18): "tarde", (0, 12): "mañana"}.get(period, "ese período")
                return f"No tienes eventos en la {period_label} de hoy."
            return f"📅 {label}: no hay eventos agendados."

        # Respuesta natural para modo voz
        if ctx and getattr(ctx, "speak", False):
            return self._format_list_for_voice(events, lower, period)

        n_ev = len(events)
        ev_word = "evento" if n_ev == 1 else "eventos"
        lines = [f"📅 {label} ({n_ev} {ev_word}):"]
        for ev in events:
            lines.append(
                "  " + _format_event_line(ev.start_ts_utc, ev.title, ev.category)
            )
        return "\n".join(lines)
