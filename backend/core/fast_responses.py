"""
Respuestas instantáneas para inputs triviales — sin LLM, sin TTS overhead de
generación. Se resuelven en 0 ms de procesamiento (solo dict lookup).

Regla de normalización: la clave es el input con `.lower().strip()`,
removiendo "zephyro" y espacios extra (idéntico a lo que hace el orchestrator
antes del lookup).

Hay dos tipos de fast-path:
  * FAST_CACHE — respuestas estáticas, dict lookup puro.
  * _DYNAMIC_HANDLERS — respuestas computadas (hora, fecha) que requieren
    datetime.now() pero no LLM. Latencia ~0ms igual.
"""

from datetime import datetime
import unicodedata

NAME = "Bruno"

_SPANISH_DAYS = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]
_SPANISH_MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

_GREETINGS: dict[str, str] = {
    "hola":          f"Hola {NAME}, ¿en qué puedo ayudarte?",
    "hey":           f"Hey {NAME}, dime",
    "buenas":        f"Buenas, {NAME}. ¿En qué puedo ayudarte?",
    "buenos días":   f"Buenos días {NAME}, ¿qué necesitas?",
    "buenos dias":   f"Buenos días {NAME}, ¿qué necesitas?",
    "buenas tardes": f"Buenas tardes {NAME}, ¿en qué te ayudo?",
    "buenas noches": f"Buenas noches {NAME}, ¿cómo estás?",
    "hi":            f"Hi {NAME}, what do you need?",
    "hello":         f"Hello {NAME}, how can I help?",
}

_FAREWELLS: dict[str, str] = {
    "adiós":       f"Hasta luego, {NAME}. Aquí estaré cuando me necesites.",
    "adios":       f"Hasta luego, {NAME}. Aquí estaré cuando me necesites.",
    "chao":        f"Chao {NAME}, que te vaya bien.",
    "chau":        f"Chau {NAME}, que te vaya bien.",
    "bye":         f"Bye {NAME}, see you later.",
    "hasta luego": f"Hasta luego, {NAME}.",
    "nos vemos":   f"Nos vemos, {NAME}.",
}

_ACKS: dict[str, str] = {
    "gracias":      f"De nada, {NAME}.",
    "muchas gracias": f"De nada, {NAME}.",
    "thanks":       f"You're welcome, {NAME}.",
    "ok":           f"Perfecto, {NAME}.",
    "okey":         f"Perfecto, {NAME}.",
    "okay":         f"Perfecto, {NAME}.",
    "vale":         f"Vale, {NAME}.",
    "dale":         f"Dale, {NAME}.",
    "perfecto":     f"Perfecto, {NAME}.",
    "listo":        f"Listo, {NAME}.",
    "entendido":    f"Entendido, {NAME}.",
    "copiado":      f"Copiado, {NAME}.",
    "de acuerdo":   f"De acuerdo, {NAME}.",
    "genial":       f"Genial, {NAME}.",
    "buenisimo":    f"Buenísimo, {NAME}.",
}

# Tabla unificada — lo que el orchestrator consulta.
FAST_CACHE: dict[str, str] = {**_GREETINGS, **_FAREWELLS, **_ACKS}


def _time_response() -> str:
    now = datetime.now()
    hh = now.strftime("%H:%M")
    return f"Son las {hh}, {NAME}."


def _date_response() -> str:
    now = datetime.now()
    weekday = _SPANISH_DAYS[now.weekday()]
    month = _SPANISH_MONTHS[now.month - 1]
    return f"Hoy es {weekday} {now.day} de {month} de {now.year}, {NAME}."


# Patrones para respuestas dinámicas (hora/fecha). El key se compara contra el
# input normalizado SIN acentos para tolerar "que hora es" y "qué hora es".
_DYNAMIC_HANDLERS: dict[str, callable] = {
    "que hora es":      _time_response,
    "que hora son":     _time_response,
    "que horas son":    _time_response,
    "que hora es ahora": _time_response,
    "dime la hora":     _time_response,
    "que dia es hoy":   _date_response,
    "que dia es":       _date_response,
    "que fecha es hoy": _date_response,
    "que fecha es":     _date_response,
    "que fecha tenemos": _date_response,
    "dime la fecha":    _date_response,
}


# ── Mic test ────────────────────────────────────────────────────────────────
# "1, 2, 3, probando." no entra por FAST_CACHE (dict exacto) ni por el fast-path
# del router: `_RE_MATH` exige que el input sea SOLO dígitos, así que la palabra
# "probando" tiraba el turno entero al pipeline cloud (CAPA 2 Haiku + embeddings
# + tool loop). Un test de micrófono no necesita un modelo para contestarse.
#
# El match NO es por substring: exige un trigger fuerte Y que todo el resto sean
# tokens de relleno/números. Sin eso, "estoy probando el código de Docker"
# matchearía — y es una pregunta real que merece el pipeline completo.
_MIC_TEST_TRIGGERS: frozenset[str] = frozenset({
    "probando", "proband",          # "proband" = corte típico del STT
    "prueba", "pruebas", "probar", "proba", "test", "testing",
    "microfono", "mic", "micro",
    "escuchas", "escucha", "escuchan", "oyes", "oye", "ois", "ois?",
})

_MIC_TEST_FILLERS: frozenset[str] = frozenset({
    "uno", "dos", "tres", "cuatro", "cinco",
    "hola", "che", "ey", "hey", "zephyro",
    "me", "se", "te", "el", "la", "de", "del", "un", "una", "y", "o",
    "bien", "ahi", "ya", "ahora", "por", "favor", "si", "no", "que", "esto",
})

_MIC_TEST_MAX_WORDS = 6


def _is_mic_test(normalized_no_accent: str) -> bool:
    words = [w.strip(".,?!¿¡") for w in normalized_no_accent.split()]
    words = [w for w in words if w]
    if not words or len(words) > _MIC_TEST_MAX_WORDS:
        return False
    has_trigger = False
    for w in words:
        if w.isdigit():
            continue
        if w in _MIC_TEST_TRIGGERS:
            has_trigger = True
            continue
        if w in _MIC_TEST_FILLERS:
            continue
        return False  # token desconocido → no es un test, es una frase real
    return has_trigger


def _mic_test_response() -> str:
    return f"Te escucho fuerte y claro, {NAME}."


def lookup(user_input: str) -> str | None:
    """
    Devuelve la respuesta pre-computada si el input corresponde a una entrada
    del fast_cache (estática) o de _DYNAMIC_HANDLERS (computada). Devuelve None
    si no hay match. La normalización replica la del orchestrator.
    """
    normalized = (
        user_input.lower()
        .strip()
        .replace(" zephyro", "")
        .replace("zephyro ", "")
        .replace("  ", " ")
        .strip()
    )
    # Permitir punctuación ligera (.,?!¿¡) que un STT o el usuario pueden
    # incluir al inicio o al final ("¿qué hora es?" debe matchear).
    normalized_stripped = normalized.strip(".,?!¿¡ ")
    static = FAST_CACHE.get(normalized_stripped)
    if static is not None:
        return static
    # Las dynamic keys están sin acento; comparamos contra una versión sin
    # diacríticos del input para que "qué hora es" y "que hora es" matcheen.
    no_accent = _strip_accents(normalized_stripped)
    handler = _DYNAMIC_HANDLERS.get(no_accent)
    if handler is not None:
        try:
            return handler()
        except Exception:
            return None
    if _is_mic_test(no_accent):
        return _mic_test_response()
    return None
