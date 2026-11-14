"""
Response Sanitizer — Normaliza el tono de Zephyro independientemente del LLM.

Garantiza que ningún provider (Claude, Haiku, Gemini, local) rompa
la personalidad: formal, eficiente, sin emojis, sin entusiasmo excesivo.
"""
import re
import unicodedata
from typing import Callable

# ── Emojis y símbolos decorativos ────────────────────────────────────────────
_EMOJI_PATTERN = re.compile(
    "[\U00010000-\U0010FFFF"   # Supplementary planes (incluye la mayoría de emojis)
    "\U0001F600-\U0001F64F"    # Emoticons
    "\U0001F300-\U0001F5FF"    # Misc symbols & pictographs
    "\U0001F680-\U0001F6FF"    # Transport & map
    "\U0001F700-\U0001F77F"    # Alchemical
    "\U0001F780-\U0001F7FF"    # Geometric extended
    "\U0001F800-\U0001F8FF"    # Supplemental arrows
    "\U0001F900-\U0001F9FF"    # Supplemental symbols
    "\U0001FA00-\U0001FA6F"    # Chess symbols
    "\U0001FA70-\U0001FAFF"    # Symbols and pictographs extended
    "\u2600-\u26FF"            # Misc symbols (☀☁⛅✨⚡etc.)
    "\u2700-\u27BF"            # Dingbats
    "\uFE00-\uFE0F"            # Variation selectors
    "\u200D"                   # Zero-width joiner
    "]+",
    flags=re.UNICODE,
)

# ── Frases entusiastas de apertura ───────────────────────────────────────────
# Elimina aperturas efusivas que algunos LLMs añaden por defecto.
_ENTHUSIASM_OPENERS = re.compile(
    r"^(?:¡+\s*)?(?:"
    r"¡+Claro|¡+Por supuesto|¡+Excelente|¡+Perfecto|¡+Genial|¡+Entendido"
    r"|¡+Hola|¡+Buenas|¡+Con gusto|¡+Encantado|¡+Desde luego"
    r"|¡+Absolutamente|¡+Fantástico|¡+Maravilloso|¡+Estupendo"
    r")[,!. ]*",
    re.IGNORECASE,
)

# ── Signos de exclamación excesivos ──────────────────────────────────────────
# Reduce múltiples "!" a uno solo y elimina los sueltos al final de respuesta.
_MULTI_EXCLAMATION = re.compile(r"!{2,}")
_TRAILING_EXCLAMATION = re.compile(r"\s*!+\s*$")


# ── TTS Filters ──────────────────────────────────────────────────────────────

# Palabras función comunes en inglés (alta precisión, bajo falso-positivo con español)
_EN_FUNCTION_WORDS = frozenset([
    'the', 'in', 'of', 'with', 'and', 'for', 'at', 'by', 'to', 'an', 'a',
    'is', 'are', 'was', 'were', 'this', 'that', 'on', 'it', 'its', 'be',
    'from', 'not', 'or', 'if', 'but', 'as', 'do', 'we', 'he', 'she', 'they',
    'you', 'my', 'your', 'our', 'their', 'his', 'her', 'have', 'has', 'had',
    'will', 'would', 'can', 'could', 'should', 'may', 'might', 'must',
    'been', 'being', 'get', 'got', 'go', 'went', 'come', 'came', 'all',
    'more', 'than', 'when', 'where', 'who', 'what', 'how', 'which',
])

# Palabras españolas frecuentes sin acentos (evitar falsos positivos en detección de inglés)
_SPANISH_COMMON = frozenset([
    'que', 'como', 'para', 'pero', 'con', 'por', 'una', 'del', 'los', 'las',
    'uno', 'son', 'ser', 'hay', 'sin', 'bien', 'muy', 'mas', 'mas', 'nada',
    'todo', 'ya', 'si', 'no', 'ni', 'me', 'te', 'se', 'le', 'tu', 'yo',
    'nos', 'mi', 'su', 'sus', 'fue', 'han', 'hoy', 'aqui', 'alli', 'este',
    'esta', 'esto', 'eso', 'esa', 'ese', 'esos', 'estas', 'estos', 'ellos',
    'ellas', 'ella', 'cada', 'sobre', 'bajo', 'entre', 'hacia', 'desde',
    'hasta', 'solo', 'tal', 'vez', 'caso', 'tipo', 'solo', 'siempre', 'nunca',
    'algo', 'nada', 'todo', 'alguien', 'nadie', 'parte', 'lado', 'forma',
    'modo', 'manera', 'vez', 'veces', 'dia', 'dias', 'ano', 'anos', 'hora',
])

_RE_TIME_RANGE = re.compile(r'(\d{1,2}:\d{2})\s*[–—]\s*(\d{1,2}:\d{2})')
_RE_CLOCK     = re.compile(r'\b(\d{1,2}):(\d{2})\b')


def _time_casual_es(h: int, m: int) -> str:
    if h == 0 and m == 0:
        return "medianoche"
    if h == 12 and m == 0:
        return "mediodía"
    period = "de la mañana" if h < 12 else ("de la tarde" if h < 20 else "de la noche")
    h12 = 12 if h == 0 else (h - 12 if h > 12 else h)
    if m == 0:
        suffix = ""
    elif m == 30:
        suffix = " y media"
    elif m == 15:
        suffix = " y cuarto"
    else:
        suffix = f" y {m}"
    return f"{h12}{suffix} {period}"


def _format_times_casual(text: str) -> str:
    """Reemplaza patrones HH:MM con hora casual en español para TTS."""
    def _range(mo):
        sh, sm = map(int, mo.group(1).split(':'))
        eh, em = map(int, mo.group(2).split(':'))
        return f"de {_time_casual_es(sh, sm)} a {_time_casual_es(eh, em)}"

    text = _RE_TIME_RANGE.sub(_range, text)
    return _RE_CLOCK.sub(lambda mo: _time_casual_es(int(mo.group(1)), int(mo.group(2))), text)


# El encabezado de fuentes puede venir decorado con markdown ("**Fuentes:**", "## Sources").
# El [*_#`> ]* tolera esos adornos antes y después de la palabra para que el bloque entero
# (títulos + URLs) se borre del texto que va a TTS.
_RE_FUENTES = re.compile(
    r'\n+\s*[*_#`>\s]*(?:Fuentes?|Sources?)\s*[*_:`\s]*.*',
    flags=re.IGNORECASE | re.DOTALL,
)
_RE_STREAM_SOURCES = re.compile(
    r'\s*[*_#`>\s]*(?:Fuentes?|Sources?)\s*[*_`\s]*(?::|$)',
    flags=re.IGNORECASE,
)
_RE_FUENTES_INLINE = re.compile(
    r'[*_#`>]*\s*(?:Fuentes?|Sources?)\s*:[*_`\s]*[^\n]+$',
    flags=re.IGNORECASE | re.MULTILINE,
)
# Links markdown: [texto](url) → texto (si no, _RE_URLS borra la URL pero deja "[texto]").
_RE_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]*\)')
_RE_ATTRIBUTION = re.compile(
    r'(?:Según|De acuerdo con|Según informó|Según reportó)\s+[A-Z][^,\.]+[,\.]\s*',
)
_RE_MD_HEADERS = re.compile(r'^#{1,6}\s+', flags=re.MULTILINE)
_RE_MD_BULLETS = re.compile(r'^\s*[•\-\*]\s+', flags=re.MULTILINE)
_RE_MD_NUMBERED = re.compile(r'^\s*\d+\.\s+', flags=re.MULTILINE)  # 1. 2. 3.
_RE_MD_BOLD = re.compile(r'\*{1,3}([^*]+)\*{1,3}')                 # **bold** / *italic* / ***both***
_RE_MD_CODE = re.compile(r'`+([^`]+)`+')                           # `code` / ```block```
_RE_URLS = re.compile(r'https?://\S+')
# Contenido display-only (RichRender lo dibuja en pantalla): tablas, bloques de
# código y LaTeX no se leen en voz alta — se eliminan enteros del texto TTS.
_RE_MD_TABLE_LINE = re.compile(r'^[ \t]*\|.*$\n?', flags=re.MULTILINE)         # | a | b |
_RE_MD_TABLE_SEP = re.compile(r'^[ \t]*[|+\-: ]{3,}[ \t]*$\n?', flags=re.MULTILINE)  # |---|---|
_RE_MD_FENCED = re.compile(r'```[\s\S]*?(?:```|\Z)')
_RE_MATH_BLOCK = re.compile(r'\$\$[\s\S]*?\$\$')
_RE_MATH_INLINE = re.compile(r'\$([^$\n]{1,200})\$')
# Imágenes markdown (charts en data URI): antes que _RE_MD_LINK, que dejaría "!alt".
_RE_MD_IMAGE = re.compile(r'!\[[^\]]*\]\([^)]*\)')


def _strip_inline_math(text: str) -> str:
    """
    Elimina $...$ solo cuando parece LaTeX real. Guard anti-precios: "cuesta
    $50 y $30" no debe perder texto — se conserva salvo que el contenido tenga
    chars TeX o sea un símbolo suelto sin dígitos ($x$, $\\pi$).
    """
    def _repl(mo):
        inner = mo.group(1)
        if re.search(r'[\\^_{}=]', inner):
            return ''
        stripped = inner.strip()
        if len(stripped) <= 3 and not any(ch.isdigit() for ch in stripped):
            return ''
        # Notación de función/complejidad: $O(n)$, $f(x)$, $T(n log n)$
        # (mismo criterio que looksLikeMath en src/rich-render.js).
        if re.fullmatch(r'[A-Za-z]{1,3}\([^()]{0,16}\)', stripped):
            return ''
        return mo.group(0)
    return _RE_MATH_INLINE.sub(_repl, text)


def _strip_english_sentences(text: str) -> str:
    """
    Omite líneas que son claramente inglés puro.

    Algoritmo de dos pases:
    1. Si alguna palabra de la línea contiene tildes/ñ → es español, conservar.
    2. Si no hay tildes/ñ: requiere ≥2 palabras función en inglés Y que
       éstas representen >30% de las palabras → strip.

    Esto evita falsos positivos con palabras españolas sin acento
    (gobierno, eventos, medidas...) y con tecnicismos (YouTube, Firefox, API).

    Si el resultado queda vacío, se devuelve el texto original para evitar
    que prepare_for_tts() entregue texto vacío a Piper (bug P3).
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        # Incluir chars acentuados en la captura para detectar español correctamente
        words = re.findall(r'[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]+', line)
        if len(words) < 3:
            result.append(line)
            continue
        # Pase 1: si alguna palabra tiene acento o ñ → línea española, conservar
        if any(re.search(r'[áéíóúüñÁÉÍÓÚÜÑ]', w) for w in words):
            result.append(line)
            continue
        # Pase 2: contar palabras función en inglés de alta confianza
        en_fn_count = sum(1 for w in words if w.lower() in _EN_FUNCTION_WORDS)
        # Requiere ≥2 marcadores ingleses Y que representen >30% → strip
        if en_fn_count >= 2 and en_fn_count / len(words) > 0.30:
            continue
        result.append(line)
    stripped = '\n'.join(result)
    if not stripped.strip():
        return text
    return stripped


def prepare_for_tts(text: str, placeholder: bool = True) -> str:
    """
    Prepara texto de respuesta para síntesis de voz (Piper TTS español).

    Elimina: sección de fuentes, atribuciones periodísticas, cabeceras markdown,
    bullets, URLs residuales y frases multi-palabra en inglés que traban a Piper.
    El texto en pantalla NO se toca — solo el que va a TTS.

    `placeholder=False` returns "" instead of "Listo." when cleanup empties a non-empty
    text — required for per-sentence chunks, where "Listo." would be spoken mid-answer.
    """
    if not text:
        return text if text is not None else ""

    # 0. Normalizar Unicode: algunos LLMs (Claude Haiku) pueden emitir
    #    decomposed Unicode (NFD) donde 'á' es 'a' + combining acute accent.
    #    Piper no reconoce combining marks separados y los lee literalmente.
    text = unicodedata.normalize('NFC', text)

    original_text = text

    # 0. Replace Unicode symbols Windows cp1252 charmap can't encode
    text = text.replace('✓', '').replace('✗', 'Error:')
    # Strip all emojis/dingbats before they hit Piper or any cp1252 console.
    # Reuses the catch-all pattern from sanitize() so new LLMs can't sneak
    # unsupported codepoints past TTS (e.g. U+2705 ✅, U+26A0 ⚠, U+2728 ✨).
    text = _EMOJI_PATTERN.sub("", text)

    # 1. Strip sección "Fuentes: ..." al final (bloque completo)
    text = _RE_FUENTES.sub('', text)
    # También en línea al final de un párrafo
    text = _RE_FUENTES_INLINE.sub('', text)

    # 2. Strip atribuciones "Según Reuters, ..."
    text = _RE_ATTRIBUTION.sub('', text)

    # 2b. Strip contenido display-only ANTES de los strips de símbolos: bloques
    #     ``` enteros (su contenido no debe filtrar # * ` a los pasos de abajo),
    #     filas/separadores de tabla Markdown y LaTeX ($$...$$ y $...$ con
    #     guard anti-precios). Todo esto se ve en pantalla pero no se lee.
    text = _RE_MD_FENCED.sub('', text)
    text = _RE_MD_TABLE_LINE.sub('', text)
    text = _RE_MD_TABLE_SEP.sub('', text)
    text = _RE_MATH_BLOCK.sub('', text)
    text = _strip_inline_math(text)
    text = _RE_MD_IMAGE.sub('', text)

    # 3. Strip cabeceras markdown
    text = _RE_MD_HEADERS.sub('', text)

    # 4. Strip bullets (• - *) y listas numeradas (1. 2.) — queda solo el texto
    text = _RE_MD_BULLETS.sub('', text)
    text = _RE_MD_NUMBERED.sub('', text)

    # 4b. Strip bold/italic (**text** → text) y backticks (`code` → code)
    text = _RE_MD_BOLD.sub(r'\1', text)
    text = _RE_MD_CODE.sub(r'\1', text)

    # 4c. Links markdown [texto](url) → texto (antes de borrar URLs sueltas)
    text = _RE_MD_LINK.sub(r'\1', text)

    # 5. Strip URLs residuales
    text = _RE_URLS.sub('', text)

    # 6. Strip líneas predominantemente en inglés
    text = _strip_english_sentences(text)

    # 6b. Convertir horas HH:MM a formato casual español para lectura natural
    text = _format_times_casual(text)

    # 7. Colapsar espacios y saltos de línea múltiples
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text).strip()

    # 8. Fallback: si tras toda la limpieza el texto quedó vacío
    #    pero el original tenía contenido, devolver un mensaje audible
    #    en vez de forzar error TTS. Si el original ya era vacío/espacios,
    #    devolver cadena vacía.
    if not text:
        if placeholder and original_text.strip():
            return "Listo."
        return ""

    return text


def make_stream_tts_cleaner() -> Callable[[str], str]:
    """
    Cleaner por-chunk para iter_stream_chunks (streaming TTS).

    prepare_for_tts() necesita el texto completo (el strip de Fuentes es
    DOTALL), así que no sirve por-oración. Este cleaner es stateful POR TURNO:
    trackea fences ``` y bloques $$ abiertos entre chunks. Crear una instancia
    FRESCA por turno — el estado no debe filtrarse entre generaciones.

    Devuelve "" para chunks display-only (tablas, código, math): un chunk que
    contiene '|' se descarta entero porque el flush por min_words de
    iter_stream_chunks puede partir una fila de tabla al medio y los
    fragmentos ("Alta |") también deben morir. iter_stream_chunks omite los
    chunks cuyo clean devuelve vacío.
    """
    state = {"in_fence": False, "in_math": False, "in_sources": False}

    def _clean(chunk: str) -> str:
        s = chunk
        # A "Fuentes:" section runs to the end of the answer (titles/URLs). The full-text
        # _RE_FUENTES needs the whole response, so per chunk it must be a sticky state.
        if state["in_sources"]:
            return ""
        if "```" in s:
            if s.count("```") % 2 == 1:
                state["in_fence"] = not state["in_fence"]
            return ""
        if state["in_fence"]:
            return ""
        if "$$" in s:
            if s.count("$$") % 2 == 1:
                state["in_math"] = not state["in_math"]
            return ""
        if state["in_math"]:
            return ""
        # Only outside display-only blocks: a "Sources:" line inside a code fence is code.
        if _RE_STREAM_SOURCES.match(s):
            state["in_sources"] = True
            return ""
        if "|" in s:
            return ""
        # Imagen markdown / data URI (charts): el base64 no tiene fin de oración
        # y llega como un chunk gigante de cola — jamás debe sintetizarse.
        if "data:image" in s or "![" in s:
            return ""
        s = _strip_inline_math(s)
        s = _RE_MD_HEADERS.sub('', s)
        s = _RE_MD_BULLETS.sub('', s)
        s = _RE_MD_NUMBERED.sub('', s)
        s = _RE_MD_BOLD.sub(r'\1', s)
        s = _RE_MD_CODE.sub(r'\1', s)
        s = _RE_MD_LINK.sub(r'\1', s)
        s = _RE_URLS.sub('', s)
        return sanitize(s).strip() if s.strip() else ""

    return _clean


# ── Fish Audio: patrones de símbolos que confunden la prosodia ───────────────
_RE_FA_EM_DASH      = re.compile(r'\s*—\s*')           # em-dash
_RE_FA_EN_RANGE     = re.compile(r'(\d)\s*–\s*(\d)')   # en-dash en rango numérico
_RE_FA_EN_DASH      = re.compile(r'\s*–\s*')            # en-dash genérico
_RE_FA_SPACE_HYPHEN = re.compile(r'\s+-\s+')            # " - " separador
_RE_FA_NUM_RANGE    = re.compile(r'(\d)-(\d)')          # "5-10" rango numérico
_RE_FA_MULTI_DASH   = re.compile(r'-{2,}')              # "--" o "---"
_RE_FA_ELLIPSIS     = re.compile(r'\.{3,}')             # "..."
_RE_FA_DOUBLE_COMMA = re.compile(r',\s*,+')             # ",," residuos
_RE_FA_SPACE_COMMA  = re.compile(r'\s+,')               # " ," → ","


def normalize_for_fishaudio(text: str) -> str:
    """
    Normalización adicional específica para Fish Audio TTS.
    Llamar DESPUÉS de prepare_for_tts().
    Convierte símbolos que Fish Audio pronuncia literalmente en pausas naturales.
    """
    if not text:
        return text
    text = _RE_FA_EM_DASH.sub(', ', text)
    text = _RE_FA_EN_RANGE.sub(r'\1 a \2', text)
    text = _RE_FA_EN_DASH.sub(', ', text)
    text = _RE_FA_SPACE_HYPHEN.sub(', ', text)
    text = _RE_FA_NUM_RANGE.sub(r'\1 a \2', text)
    text = _RE_FA_MULTI_DASH.sub(', ', text)
    text = _RE_FA_ELLIPSIS.sub('. ', text)
    text = _RE_FA_DOUBLE_COMMA.sub(',', text)
    text = _RE_FA_SPACE_COMMA.sub(',', text)
    return text.strip()


def sanitize(text: str) -> str:
    """
    Aplica todas las correcciones de tono a una respuesta del LLM.
    Devuelve el texto limpio. Operación idempotente y segura.
    """
    if not text:
        return text

    # 1. Eliminar emojis
    text = _EMOJI_PATTERN.sub("", text)

    # 2. Eliminar aperturas entusiastas
    text = _ENTHUSIASM_OPENERS.sub("", text)

    # 3. Colapsar múltiples signos de exclamación
    text = _MULTI_EXCLAMATION.sub("!", text)

    # 4. Limpiar espacios dobles generados por las sustituciones
    text = re.sub(r"  +", " ", text)

    # 5. Eliminar líneas vacías al inicio generadas por sustituciones
    text = text.lstrip("\n").strip()

    return text
