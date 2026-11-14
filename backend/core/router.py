import json
import re
import logging
from typing import Dict, Any, Literal, Optional
from urllib.parse import quote_plus

import sys
import os

logger = logging.getLogger("zephyro.router")
from core.browser_intent_schema import extract_browser_intent
from core.complexity_classifier import ComplexityClassifier
from core.intent_detector import classify_by_rules, detect_intent
from core import verbs as _verbs
from core.app_config import (
    NATIVE_APPS as _CFG_NATIVE_APPS,
    SITE_KEYWORDS as _CFG_SITE_KEYWORDS,
    APP_ALIASES as _CFG_APP_ALIASES,
    AUTO_VERBS,
    AUTO_VERBS_PATTERN,
    resolve_alias,
)

# Alias mapping: normalized shorthand → canonical site name recognized by WEB_APP_URLS.
# Applied to extracted targets/queries; uses word-boundary matching to avoid
# false positives (e.g. "x" in "netflix" does NOT trigger the alias).
_WEB_ALIASES: dict[str, str] = {
    "x":        "twitter",
    "ig":       "instagram",
    "yt":       "youtube",
    "wa":       "whatsapp",
    "vsc":      "vscode",
    "vs code":  "vscode",
}

# Site-specific search URL templates for compound commands ("abre X y busca Y").
_SITE_SEARCH_URLS: dict[str, str] = {
    "youtube":   "https://www.youtube.com/results?search_query={q}",
    "github":    "https://github.com/search?q={q}",
    "reddit":    "https://www.reddit.com/search/?q={q}",
    "twitter":   "https://x.com/search?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
}

# Media search URL templates — used by _extract_media_entities().
_MEDIA_SEARCH_URLS: dict[str, str] = {
    "youtube":       "https://www.youtube.com/results?search_query={q}",
    "youtube_music": "https://music.youtube.com/search?q={q}",
    "spotify":       "https://open.spotify.com/search/{q}",
    "netflix":       "https://www.netflix.com/search?q={q}",
}

# Keywords that signal a media playback action.
# NO derivada de core/verbs.py a propósito: varios keywords llevan espacio
# terminal ("pon ", "ver ") para evitar falsos positivos por substring
# ("ponche", "cupón"). El voseo está cubierto y el test de consistencia
# (test_verb_consistency.py) verifica que no se quede atrás.
_MEDIA_ACTION_KEYWORDS: list[str] = [
    "reproduce", "reproducir", "produce", "producir", "pon ", "ponme ", "toca",
    "escucha", "mira", "ver ", "dale a", "play ",
    "busca", "búscame", "buscame", "encuentra",
    # Voseo rioplatense
    "poné ", "ponémela ", "reproducí", "reproducime", "buscá", "tocá",
    "escuchá", "mirá",
]

# Verbos de búsqueda AMBIGUOS: "busca X" puede ser media ("busca [canción]") o búsqueda web
# ("búscame información/noticias sobre X"). Sin desambiguar, "búscame información en la ONPE"
# caía a PLAY_MEDIA → YouTube Music. Regla: si SOLO hay verbo de búsqueda (sin verbo de
# reproducción real) y aparecen indicadores de info web, NO es media → cae a búsqueda web.
_MEDIA_SEARCH_VERBS: frozenset[str] = frozenset({"busca", "búscame", "buscame", "buscá", "encuentra"})

# Verbos DÉBILES: fuera de un contexto de media son vocabulario común y no
# significan "reproducí algo". Un verbo débil solo NO alcanza para rutear a
# PLAY_MEDIA; hace falta plataforma, tipo de media u objeto de media.
# `mira`/`ver` entran acá porque "mirá el ejercicio 3" no es un pedido de
# video, y `toca` porque "tocá el botón" no es un pedido de música.
#
# `toca`/`mira` NO están acá. Estuvieron, y convertían *ambiguo* en *no es
# media*: "toca Despacito" y "mira Bohemian Rhapsody" dejaron de reproducir
# nada (medido el 2026-10-02). Ningún regex puede saber que "Despacito" es una
# canción, y un título nuevo no va a estar en ninguna lista — así que
# descartarlos era peor que el falso positivo que querían evitar.
# Para esos verbos alcanza el override de objeto de UI de abajo.
_MEDIA_WEAK_VERBS: frozenset[str] = _MEDIA_SEARCH_VERBS

# Objeto de interacción explícito: si la frase nombra uno, el verbo es de
# INTERACCIÓN y nunca de reproducción. Resuelve "toca el botón de enviar" sin
# tocar "toca Despacito".
_RE_UI_OBJECT = re.compile(
    r"\b(?:bot[oó]n|botones|enlace|link|[ií]cono|icono|casilla|checkbox"
    r"|campo|input|men[uú]|pesta[nñ]a|tab|solapa|celda|fila|columna"
    r"|tecla|barra|men[uú]\s+desplegable|desplegable|slider|switch)\b",
    re.IGNORECASE,
)

# Verbos de COLOCACIÓN: "pon X en Y" pone X dentro de Y. Solo es playback si
# Y es una plataforma ("pon X en youtube").
_MEDIA_PLACEMENT_VERBS: tuple[str, ...] = ("pon ", "ponme ", "poné ", "pone ")
_WEB_INFO_INDICATORS: tuple[str, ...] = (
    "informaci", "noticia", "datos", "página oficial", "pagina oficial", "sitio oficial",
    "en internet", "en la web", "estado actual", "estado de", "situaci",
    "qué pasó", "que paso", "qué está pasando", "que esta pasando", "investiga",
    "última hora", "ultima hora", "elecciones", "resultados de",
)

# Platform phrases → provider key (ordered longest-first to avoid overlaps).
_MEDIA_PLATFORM_MAP: dict[str, str] = {
    "youtube music": "youtube_music",
    "yt music":      "youtube_music",
    "youtube":       "youtube",
    "spotify":       "spotify",
    "netflix":       "netflix",
}

# Provider por defecto cuando el usuario no menciona plataforma
# ("produce el álbum X"). Un intent de media detectado debe ejecutarse
# siempre: si cae al LLM plano, este alucina confirmaciones sin ejecutar.
_MEDIA_DEFAULT_PROVIDER: str = "youtube_music"

# Contenido que NO es una entidad real: queda tras limpiar la frase cuando el
# usuario no dijo QUÉ reproducir ("reproduce el album" → "el album"). Buscar
# esto en YouTube Music devuelve basura, así que no se trata como PLAY_MEDIA.
# NOTA: "música" NO está acá a propósito — "pon música" cae en recommendation
# (reproduce un mix), comportamiento legítimo que no queremos romper.
_EMPTY_MEDIA_CONTENT: frozenset[str] = frozenset({
    "el", "la", "lo", "un", "una", "the",
    "album", "álbum", "el album", "el álbum", "un album", "un álbum",
    "disco", "el disco", "un disco",
    "cancion", "canción", "la cancion", "la canción", "una cancion", "una canción",
    "tema", "el tema", "playlist", "lista", "la lista",
})

# Noise words removed when extracting the content from a media command.
_MEDIA_NOISE_WORDS: list[str] = [
    "abre ", "abrir ", "open ", "pon en ",
    "búscame ", "buscame ", "busca ", "encuentra ",
    "pero ", "pero, ",   # conjunción adversativa ("pero reproduce X")
    "oye ", "oye, ", "ey ", "hey ",
    "no funciona ", "no está ", "no va ", "no se ", "no me ",
    "todavía no ", "todavia no ", "aún no ", "aun no ",
    "es ",  # "es Gold in the Ceiling" → elimina el "es" sobrante
    "este ",  # P3 fix: STT contamination - "este abre YT Music" → "este" leaks into query
    "intenta ",  # P3 fix: "intenta de nuevo" contamination
    "de nuevo ",  # P3 fix: "intenta de nuevo" contamination
    "nuevo ",  # P3 fix: "intenta nuevo" (when "de" was already stripped)
    "a ver ",  # P3 fix: conversational prefix "a ver, reproduce..."
    "por favor ", "porfavor ", "please ",  # P3 fix: polite prefixes
    "vamos a ",  # P3 fix: "vamos a reproducir..."
]
_MEDIA_CONNECTORS: list[str] = [" en el ", " en ", " para ", " a ", " y ", " del ", " and ", " de ", " e "]

# Correction/negation phrases that are NOT part of the actual media query.
# These appear when the user is correcting a previous failed search.
# We strip them from the content BEFORE any other extraction.
_MEDIA_CORRECTION_PHRASES: list[str] = [
    "no reproduce", "no reproduces", "no está reproduciendo",
    "no funciona", "no va", "no se escucha",
    "no es eso", "no es esa", "no es esa canción",
    "esa no es", "eso no es",
    "me equivoqué", "me equivoque", "corrijo",
    "todavía no", "todavia no", "aún no", "aun no",
    "no, ",  # comma-separated correction: "no, reproduce X"
    "intenta de nuevo", "intenta de nuevo, ",  # P3 fix: STT correction leak
    "inténtalo de nuevo", "abre de nuevo",  # P3 fix: more correction variants
    "vuelve a intentar", "vuelve a intentarlo",  # P3 fix
]

# Wake word / assistant-name aliases to strip from media search queries.
# Whisper often mishears "Zephyro" as "Tefiro", "Piro", "Ciro", etc.
_WAKE_WORD_PATTERNS: list[str] = [
    r'\b(zephyro|z[eé]firo|zefir|tefiro|tefilo|firo|fiero|piro|pilo|ciro|cilo|sefiro|cefir|jarvis)\b[,:\s]*',
]

# Tipos de pedido musical. Orden importante: "recommendation" antes que
# "artist" porque "pon música de X" es artist pero "pon música" sin más es
# recomendación.
_MEDIA_TYPE_PATTERNS: list[tuple[str, str]] = [
    ("recommendation", r'\b(recomi[eé]nda(?:me)?|sorpr[eé]ndeme|p[oó]n\s+algo(?:\s+de\s+m[uú]sica)?|p[oó]n\s+m[uú]sica\s*$)\b'),
    ("album",          r'\b(?:el\s+|un\s+)?[aá]lbum\s+(?:de\s+)?'),
    ("artist",         r'\b(?:m[uú]sica\s+de|canciones?\s+de|todo\s+de|temas?\s+de|cosas\s+de|(?:una?\s+)?canci[oó]n\s+aleatoria\s+de|algo\s+de)\b'),
]


# ── Regex pre-compilados (se compilan UNA vez al importar, no en cada request) ──

_RE_MATH = re.compile(r'^\s*[\d\s\+\-\*\/\(\)\.,]+\s*$')
_RE_MATH_INTENT = re.compile(
    r'\b(cuánto es|cuanto es|calcula|suma|resta|multiplica|divide)\s+[\d\+\-\*\/]'
)
_RE_CONCEPTUAL = re.compile(
    r'\b(qué es|que es|qué son|que son|explica|explicame|explícame|define'
    r'|dime qué|dime que|dime cuál|dime cual|cuál es|cual es|cómo es|como es'
    r'|qué significa|que significa|para qué sirve|para que sirve'
    r'|cómo funciona|como funciona)\b',
    re.IGNORECASE,
)
_RE_CONCEPTUAL_BRIEF = re.compile(r'\b(qué es|que es)\s+\w+\s*$')
_RE_CONCEPTUAL_ACTION = re.compile(r'\b' + _verbs.verbs_pattern("automation") + r'\b')
# Wake-word vocativo inicial ("Zephyro, …" / "Jarvis …"). Se quita SOLO para la
# clasificación heurística (no afecta el prompt al LLM): si no, "Zephyro, buenos días"
# tiene first_word="zephyro" y la detección de saludo/comando (por primera palabra) falla.
_RE_WAKE_PREFIX = re.compile(r'^\s*(?:zephyro|jarvis)\b[\s,]*', re.IGNORECASE)
_RE_DIAGNOSTIC = re.compile(
    r'\b(diagnostica|diagnostícate|diagnosticate|diagnostícame|diagnosticame)\b'
    r'|\bhaz?\s+(un\s+)?(auto[- ]?)?diagn[oó]stico\b'
    r'|\b(haz|hazme|hacer)\s+auto[- ]?diagn[oó]stico\b'
    r'|\brevisa?\s+(los\s+)?(errores|logs|problemas|bugs)\b'
    r'|\banaliza\s+(los\s+)?(errores|logs)\b'
    r'|\b(realiza|realizo|ejecuta|ejecuto|hago|necesito|quiero)\s+(un\s+)?(?:auto[- ]?)?diagn[oó]stico\b'
    # "Diagnóstico" como sustantivo independiente, opcionalmente con "tier/nivel/taller N"
    # (taller cubre el caso STT-mishearing de "tier")
    r'|^\s*(?:un\s+|el\s+)?diagn[oó]stico\b'
    r'|\bdiagn[oó]stico(?:\s+(?:con|en|usando|del?)\s+(?:el\s+|la\s+)?(?:tier|nivel|capa|taller)\s*[0-9])\b'
    # Frases coloquiales de auto-reporte: "qué te pasa", "qué tienes", "qué está pasando".
    # Guards: NO disparar diagnóstico si es una pregunta CAUSAL/externa —
    #   (?<!por ) → "por qué está pasando…" es "why X", no auto-reporte del sistema.
    #   (?!\s+(?:con|sobre|eso|esto)) → "qué está pasando con X / …eso" mira un tema externo.
    #   TILDE OBLIGATORIA → "qué" es interrogativo; "que" sin tilde es el pronombre
    #     RELATIVO y no pide nada. "las funciones que tienes" disparaba el
    #     DiagnosticAgent por matchear "que tienes". Sin tilde solo cuenta al
    #     inicio de la frase o tras "¿" (para un STT que no acentúe).
    r'|(?<!por )\bqu[éè]\s+(?:te\s+)?(?:pasa|pas[oó]|tienes|sucede)\b(?!\s+(?:con|sobre|eso|esto))'
    r'|(?:^|¿)\s*que\s+(?:te\s+)?(?:pasa|pas[oó]|tienes|sucede)\b(?!\s+(?:con|sobre|eso|esto))'
    r'|(?<!por )\bqu[éè]\s+est[aá]\s+pas(?:ando|ó)\b(?!\s+(?:con|sobre|eso|esto))'
    r'|(?:^|¿)\s*que\s+est[aá]\s+pas(?:ando|ó)\b(?!\s+(?:con|sobre|eso|esto))'
    # Follow-up queries on a running/completed diagnostic
    r'|\bya\s+tienes\s+(?:el\s+)?(?:diagn[oó]stico|resultados?|an[aá]lisis)\b'
    r'|\btienes\s+(?:los?\s+)?(?:resultados?|diagn[oó]stico)\b'
    r'|\bqu[eé]\s+encontraste\b'
    r'|\bterminaste\s+(?:el\s+)?(?:diagn[oó]stico|an[aá]lisis)\b',
    re.IGNORECASE,
)
_RE_CODE_ANALYSIS = re.compile(
    r'\b(analiza|analizar|análisis)\b.*\b(código|codigo|repo|repositorio|codebase)\b'
    r'|\b(código|codigo|repo|repositorio|codebase)\b.*\b(analiza|analizar|análisis)\b'
    r'|\b(impacto|blast radius|blast\s*radius)\b.*\b(función|funcion|clase|método|metodo|cambio)\b'
    r'|\b(dependencias|imports|call\s*graph|call\s*chain)\b.*\b(de|del|para)\b'
    r'|\b(qué|que)\s+(usa|llama|importa|depende)\b.*\b(función|funcion|clase|método|metodo)\b'
    r'|\b(quién|quien)\s+(llama|usa|consume)\b'
    r'|\b(refactor|renombrar|rename)\b.*\b(impacto|afecta|rompe)\b'
    r'|\b(arquitectura|estructura)\b.*\b(proyecto|repo|código|codigo)\b'
    r'|\b(busca|buscar|encuentra)\b.*\b(en el código|en el codigo|en el repo)\b'
    r'|/code\b',
    re.IGNORECASE,
)
# Objeto de media: lo que convierte a un verbo en un comando de reproducción.
# Sin esto, "en la evaluación continua 1" disparaba resume y el asistente respondía
# "Reproduciendo música" en medio de una consulta de notas (bug real, 2026-09-30).
# CAPA 0.6 hace `return` incondicional ~370 líneas antes de los guards
# conversacionales de CAPA 1, así que el ancla tiene que estar en el patrón mismo.
_MEDIA_OBJ = (
    r'(?:l[ao]\s+|el\s+)?'
    r'(?:m[uú]sica|canci[oó]n|tema|audio|sonido|reproducci[oó]n|playlist|video|v[ií]deo|podcast)'
)
# Un verbo pelado SÍ es comando cuando el mensaje es solo eso ("pausa", "continuá por favor").
_BARE_TAIL = r'(?:\s+(?:por\s+favor|porfa|porfis))?\s*[.!]*\s*$'

_RE_PLAYBACK_CTL: list[tuple[str, re.Pattern]] = [
    # pause: todas las formas de "pausar":
    #   imperativo/indicativo: pausa, pausas, pausar
    #   subjuntivo:            pause, pauses, pausen
    # + "pon en pausa" + "para la música"
    ("pause",  re.compile(
        r'(?:'
        rf'^\s*(?:paus[aá][rs]?|pause[ns]?){_BARE_TAIL}'                   # mensaje = solo el verbo
        r'|'
        rf'\b(?:paus[aá][rs]?|pause[ns]?)\s+{_MEDIA_OBJ}\b'                # pausa la música
        r'|'
        r'\bpon(?:la|lo)?\s+en\s+pausa\b'                              # ponla en pausa
        r'|'
        rf'\bpara\s+{_MEDIA_OBJ}\b'                                    # para la música
        r'|'
        # Mis-transcripciones comunes de "pausa" cuando la música se cuela en el
        # mic (call mode): "pase/posa/pausá/pauza la música". Ancladas al objeto
        # "(la) música/canción" para no chocar con otros comandos.
        r'\b(?:posa|pos[aá]|paus[aá]|pauza|pase|pas[eé]|pausi?a)\s+(?:la\s+)?(?:m[uú]sica|canci[oó]n)\b'
        r')',
        re.IGNORECASE,
    )),
    # resume: imperativo/indicativo (reanuda, continúa, sigue) +
    #         subjuntivo (reanudes, continues) + infinitivo (reanudar, continuar).
    # SIEMPRE con objeto de media o como mensaje completo: "continua" suelto es
    # vocabulario normal ("evaluación continua", "continuá con el ejercicio").
    ("resume", re.compile(
        rf'^\s*(?:reanud[aá][rs]?|reanude[ns]?|contin[uú]a[rs]?|continu[aá][rs]?|contin[uú]e[ns]?){_BARE_TAIL}'
        rf'|\b(?:reanud[aá][rs]?|reanude[ns]?|contin[uú]a[rs]?|continu[aá][rs]?|contin[uú]e[ns]?'
        rf'|sigue|segu[ií]|reactiva)\s+{_MEDIA_OBJ}\b'
        r'|\bvuelve\s+a\s+reproducir\b',
        re.IGNORECASE,
    )),
    ("next",   re.compile(r'\b(siguiente\s+canci[oó]n|siguiente\s+tema|pasa(?:\s+a)?\s+la\s+siguiente|salta(?:\s+(?:la\s+)?canci[oó]n)?)\b', re.IGNORECASE)),
    ("prev",   re.compile(r'\b(canci[oó]n\s+anterior|tema\s+anterior|vuelve\s+a\s+la\s+anterior|regresa\s+a\s+la\s+anterior|anterior\s+canci[oó]n)\b', re.IGNORECASE)),
    ("mute",   re.compile(
        rf'^\s*(?:silenci[aá][rs]?|silencie[ns]?){_BARE_TAIL}'
        rf'|\b(?:silenci[aá][rs]?|silencie[ns]?)\s+{_MEDIA_OBJ}\b',
        re.IGNORECASE,
    )),
    ("unmute", re.compile(r'\b(activa\s+el\s+sonido|reactiva\s+(?:el\s+)?audio|pon\s+el\s+sonido|quita\s+el\s+silencio)\b', re.IGNORECASE)),
    ("close",  re.compile(r'\bcierra\s+(?:la\s+)?(?:m[uú]sica|el\s+reproductor|la\s+ventana\s+(?:que\s+est[aá]\s+reproduciendo\s+)?(?:la\s+)?m[uú]sica)\b', re.IGNORECASE)),
]
# "Secciones" que en realidad piden la home del sitio, no una subruta.
# "abre la página de instagram" → instagram.com, NO instagram.com/la pagina.
_GENERIC_HOME_SECTIONS: frozenset[str] = frozenset({
    "la pagina", "la página", "pagina", "página", "la web", "web",
    "el sitio", "sitio", "la pagina web", "la página web",
    "el home", "home", "el inicio", "inicio", "la app", "app",
})

_RE_SEARCH_VERB_EARLY = re.compile(r'\b' + _verbs.verbs_pattern("search") + r'\b')
_RE_SEARCH_VERB = re.compile(r'\b' + _verbs.verbs_pattern("search") + r'\b')
_RE_INFO_REQUEST = re.compile(
    r'\b(dime|dame|listame|listámelos|cuéntame|cuentame|explícame|explicame'
    r'|cuáles son|cuales son|quiénes son|quienes son|quién es|quien es)\b'
)
_RE_TLD = re.compile(r'\.(com|net|org|io|ai|app|es|ar|mx|dev|info|edu|gov|uk|de|fr)$')
# BROWSE_AND_EXTRACT: "entra a [página/sitio] (y|e) dime/extrae/investiga [tema]".
# Fuente ÚNICA del patrón — usada en el guard de CAPA 1.15 (para cederle el paso) y en
# el bloque de detección CAPA 1. group(1)=sitio, group(2)=tema. inv[eé]?stigar? tolera
# el typo STT "invstiga"; el conector [ye] cubre "y dime" y "e investiga".
_RE_BROWSE_EXTRACT = re.compile(
    r'(?:entra\s+(?:a|en)|ve\s+a(?:l)?|navega\s+a|visita|ir\s+a|ingresa\s+(?:en|a))\s+'
    r'(?:(?:la\s+)?p[aá]gina\s+(?:oficial\s+)?(?:de\s+(?:la\s+)?)?)?'
    r'([\w\.\-]+(?:\.[\w]+)+(?:/\S*)?|[\w]+)\s+'
    r'(?:[ye]\s+)?'
    r'(?:dime|d[ií]me|extrae|saca|mira|rev[ií]sa|consulta|lee|busca|'
    r'mu[eé]strame|obt[eé]n|averigua|inv[eé]?stigar?|verifica|checa)\s+'
    r'(.+)'
)
# Research-on-named-site: "investiga en la página oficial de [sitio] sobre [tema]".
# Verbo de BÚSQUEDA (no navegación) → no cae en _RE_BROWSE_EXTRACT. Fuente ÚNICA del
# patrón — usada en el guard de CAPA 1.15 (para cederle el paso) y en el bloque de
# detección CAPA 1, así no vuelven a divergir. group(1)=sitio, group(2)=tema.
_RE_RESEARCH_SITE = re.compile(
    r'\b(?:inv[eé]?stigar?|averigua|consulta|busca)\b'
    r'[^.]*?\b(?:p[aá]gina|web|sitio|portal)\s+'
    r'(?:oficial\s+)?(?:de\s+(?:la\s+|el\s+|los\s+|las\s+)?)?'
    r'([\w\.\-]+)\s+'
    r'(?:sobre|acerca\s+de|respecto\s+a|de)\s+'
    r'(.+)'
)
# ── CAPA 1.1 (visión) ───────────────────────────────────────────────────
# Antes esto era `\b(pantalla|viendo|ves|mira|analiza este|captura|screenshot)\b`
# con un `return` incondicional, o sea que un verbo pelado disparaba una
# captura de pantalla. Medido el 2026-10-01, todas estas daban `vision`:
#   "ves por que me da error"            ← es una pregunta, no un pedido
#   "mira el ejercicio 3"
#   "la captura de paquetes de Wireshark" ← vocabulario diario en redes
#   "necesito la captura de la topologia"
# Para alguien que cursa redes, "captura" y "monitoreo" son palabras de todos
# los días. Ahora se exige una referencia real a la pantalla.

# Palabras que por sí solas ya se refieren a la pantalla.
_RE_VISION_KW = re.compile(
    r'\b(?:pantalla|pantallazo|screenshot|captura\s+de\s+pantalla'
    r'|captur[aá]\s+(?:la\s+)?pantalla)\b',
    re.IGNORECASE,
)
# Verbos de percepción: ambiguos solos. Solo son visión si la frase además
# menciona la pantalla o señala algo ("mirá esto").
_RE_VISION_WEAK = re.compile(
    r'\b(?:ves|viendo|mir[aá]|fijate|f[ií]jate|analiz[aá]\s+est[eo])\b',
    re.IGNORECASE,
)
_RE_SCREEN_REF = re.compile(
    r'\b(?:pantalla|pantallazo|monitor|escritorio|mi\s+pc|computadora'
    r'|esto|esta\s+imagen|esta\s+captura|ac[aá]|aqu[ií])\b',
    re.IGNORECASE,
)
_SCREEN_QUERY_PHRASES = [
    "que dice la pantalla", "que ves", "que hay en la pantalla",
    "explicame esto", "que es esto", "que tengo en pantalla",
    "que muestra", "leeme esto", "traduci esto",
    "que dice en", "que pone en",
    "lee la pantalla", "lee esto", "leeme la pantalla",
    "analiza la pantalla", "mira la pantalla",
    "describe la pantalla", "describeme la pantalla",
    # Variantes "mi pantalla" — bypasan el guard last_browser_target del bloque vision
    "mira mi pantalla", "ve mi pantalla", "ves mi pantalla",
    "analiza mi pantalla", "describe mi pantalla", "describeme mi pantalla",
    "que hay en mi pantalla", "que ves en mi pantalla",
    "leeme mi pantalla", "lee mi pantalla",
]
# Patrón combinado para apps nativas (actualizado en __init__ de IntentRouter con la lista real).
# Se define aquí como placeholder y se sobreescribe en la clase.
_RE_NATIVE_APPS: re.Pattern = re.compile(
    r'\b(' + '|'.join(re.escape(a) for a in _CFG_NATIVE_APPS) + r')\b',
    re.IGNORECASE,
)
_RE_THANKS = re.compile(r'\b(gracias|thanks|thank you|muchas gracias)\b', re.IGNORECASE)
_RE_CLICK_TARGET_CLEAN = re.compile(
    r'^(en\s+el\s+botón\s+|en\s+el\s+boton\s+|en\s+el\s+|en\s+la\s+|en\s+)'
)
# Shopping / e-commerce intent
_RE_SHOPPING = re.compile(
    r'\b(cu[aá]nto\s+cuesta|cu[aá]nto\s+vale|precio\s+de|busca\s+en\s+(?:aliexpress|mercadolibre|ebay)'
    r'|buscar\s+en\s+(?:aliexpress|mercadolibre|ebay)'
    r'|comparar\s+precios?|m[aá]s\s+barato|mejor\s+precio'
    r'|buscar?\s+producto|encontrar\s+(?:un\s+)?producto'
    r'|busca\s+(?:un\s+)?(?:buen\s+)?precio|d[oó]nde\s+(?:comprar|conseguir)'
    r'|comprar\s+(?:un\s+|una\s+)?(?:\w+\s+){1,5}(?:barato|online|por\s+internet)'
    r'|en\s+(?:aliexpress|ebay|mercadolibre)\s+(?:busca|encuentra|hay))\b',
    re.IGNORECASE,
)
# Excluir frases filosóficas/abstractas donde "cuánto cuesta" no es e-commerce
_RE_SHOPPING_ABSTRACT = re.compile(
    r'\b(la\s+vida|el\s+tiempo|la\s+libertad|la\s+felicidad|el\s+amor|la\s+paz'
    r'|ser\s+(?:buena?\s+)?persona|hacerlo|ser\s+honesto|ser\s+feliz'
    r'|existir|vivir\s+bien)\b',
    re.IGNORECASE,
)


def _detect_media_type(lower_text: str) -> tuple[str, str]:
    """Devuelve (media_type, frase_a_remover_para_obtener_seed)."""
    for kind, pattern in _MEDIA_TYPE_PATTERNS:
        m = re.search(pattern, lower_text, flags=re.IGNORECASE)
        if m:
            return kind, m.group(0)
    return "song", ""


def _split_album_artist(
    lower: str, platform_text: str, type_phrase: str
) -> tuple[str | None, str | None]:
    """
    Separa "{álbum} de {artista}" preservando el " de " que el pipeline normal
    de _extract_media_entities elimina como conector.

    Usa el ÚLTIMO " de " como separador (el nombre del álbum puede contener
    "de", p.ej. "el lado oscuro de la luna de pink floyd"). La validación real
    ocurre al ejecutar: si la página del artista no existe, el caller trata
    todo como nombre de álbum y cae a la búsqueda con match por nombre.

    Devuelve (album, artist) o (None, None) si no hay separador claro.
    """
    content = lower
    for kw in _MEDIA_ACTION_KEYWORDS:
        content = re.sub(r'\bver\b', ' ', content) if kw == "ver " else content.replace(kw, " ")
    if platform_text:
        content = content.replace(platform_text, " ")
    if type_phrase:
        content = re.sub(re.escape(type_phrase), " ", content, flags=re.IGNORECASE)
    # Conectores EXCEPTO " de ": lo necesitamos como separador álbum/artista.
    for connector in _MEDIA_CONNECTORS:
        if connector.strip() == "de":
            continue
        content = content.replace(connector, " ")
    for noise in _MEDIA_NOISE_WORDS:
        content = content.replace(noise, " ")
    for pat in _WAKE_WORD_PATTERNS:
        content = re.sub(pat, ' ', content, flags=re.IGNORECASE)
    content = " ".join(content.split()).strip(' ,.:;')
    if " de " not in content:
        return None, None
    left, right = content.rsplit(" de ", 1)
    # Limpiar bordes: el álbum no arranca con conectores sueltos; del artista
    # quitamos conectores finales (pero NO 'the', que es parte de nombres).
    left = re.sub(
        r'^(?:pero|oye|ey|hey|en|a|y|del|para|ok|okay|and|es|este|esta|nuevo)\s+',
        '', left,
    ).strip(' ,.:;')
    right = re.sub(r'\s+(?:en|del|a|para|por|y|and)\s*$', '', right).strip(' ,.:;')
    if not left or not right:
        return None, None
    return left, right


def _extract_media_entities(text: str) -> dict | None:
    """
    Extracts action/content/platform from a media playback instruction,
    regardless of word order.  Returns None if no media intent is detected.

    Handles all equivalent phrasings:
        "reproduce X en youtube"
        "abre youtube y reproduce X"
        "en youtube reproduce X"
        "reproduce X en yt music"
    """
    lower = text.lower().strip()

    # 0a. Strip correction phrases BEFORE any other processing.
    #     This handles "No reproduce, Gold in the Ceiling..." where
    #     "No reproduce" is a correction, not part of the query (bug P2).
    for phrase in _MEDIA_CORRECTION_PHRASES:
        lower = lower.replace(phrase, " ")

    # 0b. Detect media type. Se calcula ACÁ, antes de la desambiguación de
    #     verbos débiles, porque esa decisión necesita saber si hay señal de
    #     media en la frase.
    media_type, type_phrase = _detect_media_type(lower)

    _mentions_platform = any(p in lower for p in _MEDIA_PLATFORM_MAP)
    # OJO: `media_type` NO sirve como señal — `_detect_media_type` devuelve
    # "song" por DEFAULT cuando no matcheó nada (`return "song", ""`). La señal
    # real es `type_phrase`, que queda vacía en ese caso.
    _has_media_signal = bool(
        _mentions_platform or type_phrase or re.search(_MEDIA_OBJ, lower)
    )

    # 0a-bis. Verbos DÉBILES: ambiguos fuera de un contexto de media.
    #     Antes la regla era al revés — pedía una señal POSITIVA de info web
    #     (`_WEB_INFO_INDICATORS`) para NO tratarlo como música. O sea que el
    #     default de "busca <cualquier cosa>" era YouTube Music. Medido el
    #     2026-10-01: "busca el teorema de Bayes", "busca el enunciado del
    #     TP2", "buscame el apunte de estructuras" y "encuentra el error en
    #     el stacktrace" todas reproducían música.
    #
    #     "busca X" y "poné X" son genuinamente ambiguos: ningún regex
    #     distingue "busca Bad Bunny" de "busca el teorema de Bayes". Ante la
    #     duda, el default correcto es búsqueda web: buscar algo que no era
    #     es inofensivo, y poner música que nadie pidió interrumpe. Para pedir
    #     música quedan los verbos fuertes ("reproducí", "play") o nombrar la
    #     plataforma, que siguen funcionando igual.
    # Un objeto de UI gana sobre cualquier verbo: "toca el botón de enviar" es
    # una interacción, no un pedido de música, y no hace falta señal de media
    # para descartarlo.
    if _RE_UI_OBJECT.search(lower) and not _has_media_signal:
        return None

    _has_weak_verb = any(v in lower for v in _MEDIA_WEAK_VERBS)
    _has_strong_verb = any(
        kw in lower for kw in _MEDIA_ACTION_KEYWORDS
        if kw.strip() not in _MEDIA_WEAK_VERBS
    )
    if _has_weak_verb and not _has_strong_verb and not _has_media_signal:
        return None

    # 0a-ter. "pon X en Y" es una COLOCACIÓN, no playback, salvo que Y sea una
    #     plataforma. "pon la fecha del parcial en el informe" y "ponme el
    #     promedio en la planilla" reproducían música; "pon Bad Bunny en
    #     youtube" y "pon música de los Redondos" tienen que seguir andando.
    if any(kw in lower for kw in _MEDIA_PLACEMENT_VERBS) and not _has_media_signal:
        _m_en = re.search(r"\s+en\s+(.+)$", lower)
        if _m_en and not any(p in _m_en.group(1) for p in _MEDIA_PLATFORM_MAP):
            return None

    # 1. Require at least one media action keyword OR un tipo de recomendación detectado.
    if not any(kw in lower for kw in _MEDIA_ACTION_KEYWORDS) and media_type != "recommendation":
        return None

    # 2. Detect platform (longest match first to avoid "youtube" eating "youtube music").
    provider = "unknown"
    platform_text_to_remove = ""
    for platform_phrase, platform_key in _MEDIA_PLATFORM_MAP.items():
        if platform_phrase in lower:
            provider = platform_key
            platform_text_to_remove = platform_phrase
            break

    # 3. Extract content: strip action keywords, platform, connectors, and open-verb noise.
    content = lower
    for kw in _MEDIA_ACTION_KEYWORDS:
        if kw == "ver ":
            # Word-boundary replace: avoid stripping "ver" from inside words like "never".
            content = re.sub(r'\bver\b', ' ', content)
        else:
            content = content.replace(kw, " ")
    if platform_text_to_remove:
        content = content.replace(platform_text_to_remove, " ")
    # Quitar la frase de tipo (ej. "música de", "álbum", "recomiéndame") para
    # quedarnos con el seed limpio.
    if type_phrase:
        content = re.sub(re.escape(type_phrase), " ", content, flags=re.IGNORECASE)
    for connector in _MEDIA_CONNECTORS:
        content = content.replace(connector, " ")
    for noise in _MEDIA_NOISE_WORDS:
        content = content.replace(noise, " ")
    # Strip wake-word/assistant-name aliases from the query.
    for pat in _WAKE_WORD_PATTERNS:
        content = re.sub(pat, ' ', content, flags=re.IGNORECASE)
    content = " ".join(content.split()).strip(' ,.:;')
    # Strip leading connector words and clitic pronouns (me/nos/te) that survive
    # after action-keyword removal (e.g. "reproduceme" → "me <query>").
    content = re.sub(r'^(?:me|nos|te|les|le|se|no)\s+', '', content)
    content = re.sub(r'^(?:pero|oye|ey|hey|en|a|y|de|del|para|ok|okay|and|es)\s+', '', content)
    # P3 fix: strip STT contamination prefixes that survive noise-word removal
    content = re.sub(r'^(?:este|esta|intenta|int[eé]ntalo|nuevo|a\s+ver)\s+', '', content)
    # Strip trailing connectors that survive extraction
    content = re.sub(r'\s+(?:y|and|en|de|del|a|para|por|the)\s*$', '', content)
    content = content.strip()

    # Para "recommendation" sin seed, content puede quedar vacío — eso es válido.
    # Para el resto, si el contenido quedó vacío o es solo una palabra-tipo sin
    # entidad real ("el album", "album", "la canción"...), NO hay query útil:
    # devolvemos None para que no se reproduzca una búsqueda basura como "el album".
    if media_type != "recommendation" and (not content or content in _EMPTY_MEDIA_CONTENT):
        return None

    # 4. Álbum + artista: separar "{álbum} de {artista}" para poder ir a la
    #    página del artista y buscar el álbum por nombre (mucho más confiable
    #    que filtrar "Álbumes" con el string combinado, que rankea mal).
    album_name: str | None = None
    artist_name: str | None = None
    if media_type == "album":
        album_name, artist_name = _split_album_artist(lower, platform_text_to_remove, type_phrase)

    # 5. Sin plataforma explícita → provider por defecto. Nunca devolvemos
    #    "unknown": eso mandaba la frase al LLM plano, que alucinaba
    #    "Reproduciendo..." sin ejecutar nada.
    if provider == "unknown":
        provider = _MEDIA_DEFAULT_PROVIDER

    # 6. Build search URL for known platforms.
    url: str | None = None
    tmpl = _MEDIA_SEARCH_URLS.get(provider)
    if tmpl:
        url = tmpl.format(q=quote_plus(content))

    return {
        "automation_intent": "PLAY_MEDIA",
        "provider": provider,
        "search_query": content,
        "media_type": media_type,
        "url": url,
        # album/artist separados (None si no había "{álbum} de {artista}").
        "album": album_name,
        "artist": artist_name,
        # Mantenido por compatibilidad de API: siempre False (hay default).
        "needs_clarification": False,
    }


# ── Resolución del target de una app ────────────────────────────────────
# Aliases que son además vocabulario español de todos los días. Cuando uno de
# estos aparece seguido de "de"/"del" es un MODIFICADOR de otra cosa, no la app
# que el usuario quiere abrir. Medido el 2026-10-01:
#   "abre el codigo del tp2"      → abría VS Code
#   "abre los archivos del tp"    → abría el explorador
#   "activa el monitor de red"    → abría el administrador de tareas
#   "abre la terminal de pagos"   → abría cmd
# Para un estudiante de ingeniería, "código", "archivos" y "monitor" son
# palabras de uso diario; que cada una abra un programa hace al asistente
# imprevisible.
_GENERIC_WORD_APPS = frozenset({
    "codigo", "código", "archivos", "monitor", "terminal", "navegador",
    "browser", "notas", "correo",
})

# Alias COMPUESTOS: nombres de producto que contienen "de". La regla del
# modificador los descartaba —"abre la terminal de Windows" terminaba en un
# Edge en blanco (medido el 2026-10-02)— porque `de` introduce tanto contenido
# ("código **del** TP2") como un calificador de app ("terminal **de** Windows").
# Se reconocen ANTES que los débiles, así que el compuesto le gana a su primera
# palabra.
_COMPOUND_APP_ALIASES: dict[str, str] = {
    "terminal de windows": "wt",
    "windows terminal": "wt",
    "bloc de notas": "notepad",
    "visual studio code": "code",
    "editor de codigo": "code",
    "editor de código": "code",
    "administrador de tareas": "taskmgr",
    "explorador de archivos": "explorer",
    "navegador de archivos": "explorer",
    "linea de comandos": "cmd",
    "línea de comandos": "cmd",
    "simbolo del sistema": "cmd",
    "símbolo del sistema": "cmd",
}

# Límites del objeto directo de un verbo. Después de uno de estos, lo que
# sigue es un complemento y no la app que se pidió abrir: en "abre Word **con**
# el documento PowerPoint" el objeto de `abre` es Word.
_RE_OBJECT_BOUNDARY = re.compile(
    r"\b(?:con|usando|para|sobre|desde|en\s+el\s+que|mientras|y\s+luego"
    r"|despu[eé]s|adem[aá]s)\b|[,;.]",
    re.IGNORECASE,
)

# "codigo del tp" / "monitor de red": el alias modifica al sustantivo que
# sigue. No se exige nada más porque "abre el bloc de notas" se resuelve antes,
# por el match más largo (`_resolve_app_target` ordena por longitud).
_MODIFIER_TAIL = r"\s+(?:de|del)\b"


def _resolve_app_target(user_input_lower: str, apps) -> str:
    """Devuelve la app mencionada en la frase, o "unknown".

    Tres reglas, y las tres son correcciones de bugs medidos:

    1. **Word-boundary, igual que el gate.** El gate de `app_hit` ya usaba
       `\\b`, pero este resolvedor usaba `app in user_input_lower` — substring
       puro. El portero y el resolvedor miraban universos distintos, así que
       "abre el password de la wifi" abría Word (`word` ⊂ `password`),
       "activa el monitoreo de la red" abría el administrador de tareas, y
       "abre el knowledge graph" abría Edge.

    2. **Match más largo primero.** Antes recorría el iterable y cortaba en el
       primer match, o sea que ganaba el orden de declaración y no la
       especificidad: "bloc" le ganaba a "bloc de notas", y "teams" a
       "teamspeak".

    3. **Un alias genérico seguido de "de"/"del" es un modificador**, no el
       target. Ver `_GENERIC_WORD_APPS`.
    """
    # 4. Los alias COMPUESTOS se reconocen primero, en toda la frase: son
    #    nombres de producto que contienen "de" y la regla 3 los descartaría.
    for compound, canonical in _COMPOUND_APP_ALIASES.items():
        if re.search(rf"\b{re.escape(compound)}\b", user_input_lower):
            return canonical

    # 5. El objeto directo del verbo, no la frase entera. Buscar aliases en
    #    todo el texto y quedarse con el más largo elige mal por construcción:
    #    en "abre Word con el documento PowerPoint" ganaba PowerPoint, que es
    #    más largo y está en un complemento (medido el 2026-10-02). Se corta en
    #    el primer límite de objeto y se busca ahí; si no aparece nada, se
    #    reintenta sobre la frase completa para no perder los casos en que el
    #    corte quedó demasiado temprano.
    boundary = _RE_OBJECT_BOUNDARY.search(user_input_lower)
    spans = [user_input_lower[: boundary.start()]] if boundary else []
    spans.append(user_input_lower)

    # Se busca también entre las claves de APP_ALIASES para que las entradas
    # multi-palabra ("microsoft word") puedan ganar por longitud antes de que
    # la regla 3 descarte su primera palabra.
    candidates = sorted(set(apps) | set(_CFG_APP_ALIASES), key=len, reverse=True)
    for span in spans:
        if not span.strip():
            continue
        for app in candidates:
            m = re.search(rf"\b{re.escape(app)}\b", span)
            if not m:
                continue
            if app in _GENERIC_WORD_APPS and re.match(
                _MODIFIER_TAIL, span[m.end():]
            ):
                continue
            return app
    return "unknown"


class IntentRouter:
    def __init__(self, llm_manager: Any, context_manager: Optional[Any] = None):
        self.llm = llm_manager
        self.context = context_manager
        self.default_browser: str = "msedge"
        self.classifier: ComplexityClassifier = ComplexityClassifier()
        self._turn_counter: int = 0          # Contador global de turnos
        self._last_automation_turn: int = -1  # Turno de la última automatización real
        self._browser_intent_cache: dict[str, dict] = {}  # cache LLM browser extraction

    async def route(self, user_input: str) -> Literal["chat", "automation", "memory_query"]:
        """Clasifica la intención del usuario de forma rápida"""
        intent = await detect_intent(user_input)
        if intent == "command":
            return "automation"
        return "chat"

    async def classify_and_parse(self, user_input: str, skip_llm: bool = False) -> Dict[str, Any]:
        """
        Clasifica la intención del usuario y extrae parámetros.
        CAPA 0: Fast-path heurístico para queries triviales (sin LLM)
        CAPA 1: Heurística para comandos de sistema
        CAPA 2: LLM para casos complejos
        """
        # Quitar el wake-word vocativo inicial para las heurísticas (greeting/command se
        # detectan por primera palabra; "Zephyro, buenos días" rompía esa detección).
        # `normalize_voseo` pasa el voseo imperativo a su forma tuteo, que es
        # la que escriben TODOS los regex del router. Sin esto, el router era
        # el único consumidor de `core/verbs.py` que NO normalizaba:
        # `skills/registry.py` lo aplica en sus dos puntos de dispatch
        # (:94 y :201) y acá no se llamaba nunca.
        #
        # Lo que costaba, medido el 2026-10-02: "cerrá word" caía en el default
        # `action="open"` porque la regex de cierre solo tiene las formas en
        # tuteo → **pedir cerrar Word lo ABRÍA** (4 de 4 apps probadas).
        # Se normaliza solo la copia en minúsculas que usan los regex;
        # `user_input` queda intacto para extraer targets y queries con las
        # palabras del usuario.
        user_input_lower = _verbs.normalize_voseo(
            _RE_WAKE_PREFIX.sub("", user_input.lower())
        )
        word_count = len(user_input.split())

        # Incrementar turno global
        self._turn_counter += 1

        # Clasificar complejidad primero (es rápido, solo features)
        avg_latency = 0.5
        ctx = self.context
        if ctx is not None:
            # Intentar obtener latencia real
            try:
                avg_latency = min(ctx.get_average_latency("information", "simple"), 2.0)
            except:
                pass
        
        complexity_info = self.classifier.classify(
            user_input,
            latency_hint=avg_latency,
        )

        # --- CAPA 0: FAST-PATH (Sin LLM) ---
        # Detectar saludos, despedidas y consultas triviales

        # CAPA 0: Saludos, despedidas y acknowledgments via classify_by_rules (sin costo LLM)
        _rule = classify_by_rules(user_input_lower)
        if _rule in ("greeting", "farewell", "acknowledgment"):
            _template = "thanks" if _rule == "acknowledgment" else _rule
            return {
                "intent": "information",
                "fast_path": True,
                "response_template": _template,
                "complexity": "simple",
                "complexity_score": 0.0,
                "_source": "capa0_rules",
            }

        # Si es corto (≤5 palabras) y coincide con agradecimientos
        if word_count <= 5:
            if _RE_THANKS.search(user_input_lower):
                return {
                    "intent": "information",
                    "fast_path": True,
                    "response_template": "thanks",
                    "complexity": "simple",
                    "complexity_score": 0.1,
                }
        
        # Matemática simple (sin importar longitud)
        if _RE_MATH.search(user_input_lower) or _RE_MATH_INTENT.search(user_input_lower):
            return {
                "intent": "information",
                "fast_path": True,
                "response_template": "math",
                "query": user_input,
                "complexity": "simple",
                "complexity_score": 0.2,
            }

        # Detectar preguntas conceptuales con nivel de detalle
        if _RE_CONCEPTUAL.search(user_input_lower):
            _temporal_skip = [
                "actualmente", "ahora", "hoy", "este año", "en este momento",
                "últimas noticias", "quién gobierna", "quien gobierna",
                "quién es el presidente", "quien es el presidente",
                "quién es la presidenta", "quien es la presidenta",
                # Lanzamientos/eventos recientes: no son conceptos atemporales,
                # deben llegar a las capas con búsqueda web (cutoff del LLM).
                "hace pocos días", "hace pocos dias", "hace poco",
                "recientemente", "recién", "recien salió", "recien salio",
                "nuevo modelo de", "última versión de", "ultima version de",
                "último modelo de", "ultimo modelo de",
                "acaba de salir", "acaba de lanzar", "acaba de anunciar",
            ]
            is_temporal = any(m in user_input_lower for m in _temporal_skip)
            if not is_temporal and not _RE_CONCEPTUAL_ACTION.search(user_input_lower):
                detail_level = "normal"
                if any(kw in user_input_lower for kw in ["detallado", "completo", "profundo", "extenso", "a fondo", "en detalle", "explica", "explicame", "explícame"]):
                    detail_level = "detailed"
                elif any(kw in user_input_lower for kw in ["breve", "brevemente", "resumido", "resumen", "corto", "rápido", "rapido"]) or \
                     (_RE_CONCEPTUAL_BRIEF.search(user_input_lower) and len(user_input.split()) <= 5):
                    detail_level = "brief"
                
                # REGLA 3: Casos intermedios mantienen "normal" (3b con preset concepto_normal)
                
                return {
                    "intent": "information",
                    "fast_path": False,
                    "response_template": "concept",
                    "detail_level": detail_level,
                    "query": user_input,
                    "complexity": "simple" if detail_level == "brief" else complexity_info["label"],
                    "complexity_score": complexity_info["probability"],
                }

        # --- CAPA 0.4: DIAGNOSTIC (auto-análisis de logs del backend) ---
        if _RE_DIAGNOSTIC.search(user_input_lower):
            return {
                "intent": "diagnostic",
                "action": "self_diagnose",
                "fast_path": False,
                "query": user_input,
                "complexity": "complex",
                "complexity_score": complexity_info["probability"],
            }

        # --- CAPA 0.5: CODE ANALYSIS (GitNexus) ---
        if _RE_CODE_ANALYSIS.search(user_input_lower):
            # Extraer símbolo o query de la petición
            code_query = user_input  # El orchestrator lo refinará
            return {
                "intent": "code_analysis",
                "query": code_query,
                "fast_path": False,
                "complexity": "complex",
                "complexity_score": complexity_info["probability"],
            }

        # --- CAPA 0.6: PLAYBACK CONTROL (pause/next/prev/mute/stop) ---
        for _ctl_action, _ctl_pat in _RE_PLAYBACK_CTL:
            if _ctl_pat.search(user_input_lower):
                self._last_automation_turn = self._turn_counter
                return {
                    "intent": "automation",
                    "automation_intent": "PLAYBACK_CONTROL",
                    "control_action": _ctl_action,
                    "fast_path": True,
                    "complexity": "simple",
                    "complexity_score": 0.1,
                    "_source": "capa0_6_playback_ctl",
                }

        # --- CAPA 1: HEURÍSTICA (Detección por palabras clave) ---

        # 1.0 Obtener contexto de última automatización (necesario para todo CAPA 1)
        last_browser_target: Optional[str] = None
        last_payload = None
        ctx = self.context
        if ctx is not None:
            last_snapshot = ctx.get_last_automation()
            if isinstance(last_snapshot, dict):
                last_payload = last_snapshot.get("analysis")
                if isinstance(last_payload, dict):
                    candidate_target = last_payload.get("target")
                    _prev_automation_intent = last_payload.get("automation_intent", "")
                    if isinstance(candidate_target, str):
                        browser_binaries = {"firefox", "chrome", "navegador", "browser", "edge", "msedge"}
                        if candidate_target in browser_binaries:
                            last_browser_target = candidate_target
                        elif _prev_automation_intent == "NAVIGATE" and candidate_target:
                            # Sitio web navegado recientemente → contexto para follow-ups
                            # ("entra a Reels" después de "abre Instagram")
                            last_browser_target = candidate_target
        
        # 1.1 Visión (Ajustado para no sombrear browser screenshots si hay navegador abierto)
        # Una referencia explícita a la pantalla alcanza sola; un verbo de
        # percepción necesita además que la frase señale la pantalla.
        is_generic_screenshot = bool(
            _RE_VISION_KW.search(user_input_lower)
            or (
                _RE_VISION_WEAK.search(user_input_lower)
                and _RE_SCREEN_REF.search(user_input_lower)
            )
        )
        
        # Si NO hay navegador activo, o si la frase es explícita para sistema
        if is_generic_screenshot and (last_browser_target is None or any(x in user_input_lower for x in ["escritorio", "mi pc", "toda la pantalla", "computadora"])):
            return {
                "intent": "vision", 
                "action": "capture_analyze", 
                "query": user_input,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
            }

        # 1.1.3 Screen Query — pregunta sobre lo que el usuario ve en pantalla
        if any(phrase in user_input_lower for phrase in _SCREEN_QUERY_PHRASES):
            return {
                "intent": "screen_query",
                "query": user_input,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
            }

        # 1.1.5 Interacción Web (Playwright) - Requiere contexto de navegador previo o keyword explícita
        click_keywords = ["haz click", "click en", "clic en", "presiona", "pulsa", "toca el boton", "toca en"]
        type_keywords = ["escribe", "escríbele", "teclea", "ingresa", "pon en el campo", "redacta"]
        scroll_keywords = ["scroll", "baja la página", "sube la página", "baja la pagina", "sube la pagina", "desplázate", "desplazate"]
        extract_keywords = ["qué dice", "que dice", "lee la página", "lee la pagina", "extrae el texto", "resumeme la pagina", "resumeme la página"]
        screenshot_keywords = ["captura de pantalla", "screenshot", "pantallazo", "foto a la web"]

        # Si hay contexto de navegador (lo abrimos antes) O el usuario menciona explícitamente "en el navegador/firefox"
        browser_context_active = last_browser_target is not None or "navegador" in user_input_lower or "firefox" in user_input_lower or "edge" in user_input_lower

        if browser_context_active:
            # CLICK
            for kw in click_keywords:
                if kw in user_input_lower:
                    target_element = ""
                    idx_kw = user_input_lower.find(kw)
                    if idx_kw != -1:
                        target_element = user_input[idx_kw + len(kw):].strip()
                    target_element = _RE_CLICK_TARGET_CLEAN.sub('', target_element.lower()).strip()
                    return {
                        "intent": "web_search",
                        "action": "browser_click",
                        "target": target_element,
                        "complexity": complexity_info["label"],
                        "complexity_score": complexity_info["probability"]
                    }
            
            # TYPE
            for kw in type_keywords:
                if kw in user_input_lower:
                    content = ""
                    idx_kw = user_input_lower.find(kw)
                    if idx_kw != -1:
                        content = user_input[idx_kw + len(kw):].strip()
                    return {
                        "intent": "web_search",
                        "action": "browser_type",
                        "target": content,
                        "complexity": complexity_info["label"],
                        "complexity_score": complexity_info["probability"]
                    }
            
            # SCROLL
            if any(kw in user_input_lower for kw in scroll_keywords):
                direction = "up" if "sube" in user_input_lower or "arriba" in user_input_lower else "down"
                return {
                    "intent": "web_search",
                    "action": "browser_scroll",
                    "target": direction,
                    "complexity": complexity_info["label"],
                    "complexity_score": complexity_info["probability"]
                }
                
            # EXTRACT
            if any(kw in user_input_lower for kw in extract_keywords):
                return {
                    "intent": "web_search",
                    "action": "browser_get_text",
                    "target": "body",
                    "complexity": complexity_info["label"],
                    "complexity_score": complexity_info["probability"]
                }

            # SCREENSHOT
            if any(kw in user_input_lower for kw in screenshot_keywords):
                 return {
                    "intent": "web_search",
                    "action": "browser_screenshot",
                    "target": "full",
                    "complexity": complexity_info["label"],
                    "complexity_score": complexity_info["probability"]
                }

        # ── Normalizador STT: subjuntivo formal → imperativo ─────────────────
        # Groq/Whisper STT a veces produce "abra" (subjuntivo) en vez de "abre"
        # (imperativo). Normalizamos temprano para que todos los guards y regex
        # vean la forma imperativa, sin necesidad de listar cada conjugación.
        _STT_NORM_MAP = {
            "abra": "abre", "abras": "abre", "abran": "abre",
            "lance": "lanza", "lances": "lanza", "lancen": "lanza",
            "ejecute": "ejecuta", "ejecutes": "ejecuta", "ejecuten": "ejecuta",
            "inicie": "inicia", "inicies": "inicia", "inicien": "inicia",
            "active": "activa", "actives": "activa", "activen": "activa",
            "busque": "busca", "busques": "busca", "busquen": "busca",
        }
        _normalized = False
        for _subj, _imp in _STT_NORM_MAP.items():
            if re.search(rf"\b{_subj}\b\s+\w", user_input_lower):
                user_input = re.sub(rf"\b{re.escape(_subj)}\b", _imp, user_input)
                # Misma normalizacion de voseo que arriba: estas dos
                # re-derivaciones la perdian.
                user_input_lower = _verbs.normalize_voseo(user_input.lower())
                _normalized = True
        if _normalized:
            logger.debug("[router] STT normalized in input: %r", user_input[:100])

        # 1.14b Búsqueda explícita en internet (prioridad antes del bloque de automatización)
        _direct_internet_phrases = [
            "en internet", "en google", "en la web", "online",
            "en la red", "en linea", "en línea",
            "busca en internet", "busca en google", "busca en la web",
            "encuentra en internet", "encuentra en google",
        ]
        if (_RE_SEARCH_VERB_EARLY.search(user_input_lower) and
                any(phrase in user_input_lower for phrase in _direct_internet_phrases)):
            return {
                "intent": "information",
                "action": "web_search",
                "query": user_input,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
                "needs_web_search": True,
            }

        # 1.14c Shopping / Comparación de precios — antes de 1.15 para que no se vaya a automation
        if _RE_SHOPPING.search(user_input_lower) and not _RE_SHOPPING_ABSTRACT.search(user_input_lower):
            return {
                "intent": "product_search",
                "query": user_input,
                "fast_path": False,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
                "_source": "capa1_14c_shopping",
            }

        # 1.15 Búsqueda informativa (Tavily/web) — "busca noticias", "busca información"
        # Si el usuario dice "busca" + indicadores de que quiere INFORMACIÓN de vuelta
        # (no abrir un navegador), rutearlo a information para que Tavily lo maneje.
        # Definir site_keywords y apps aquí para poder usarlos en la condición.
        site_keywords = _CFG_SITE_KEYWORDS
        apps = _CFG_NATIVE_APPS

        _info_search_indicators = [
            "noticias", "noticia", "situación actual", "situacion actual",
            "qué pasa con", "que pasa con", "qué está pasando", "que esta pasando",
            "y dime", "y cuéntame", "y cuentame", "y explica",
            "dime sobre", "dime la situacion", "dime el estado",
            "háblame de", "hablame de",
            "información sobre", "informacion sobre",
            "qué sabes de", "que sabes de",
            "actualidad", "estado actual",
            "últimas noticias", "ultimas noticias",
            "en internet", "en google", "en la web", "online", "en la red",
            "en linea", "en línea",
            # Pedidos de datos oficiales / actualizados ("búscame información más actualizada
            # en la página oficial de la ONPE") — antes no matcheaban y caían a media/automation.
            "página oficial", "pagina oficial", "sitio oficial", "web oficial",
            "más actualizada", "mas actualizada", "más actualizado", "mas actualizado",
            "más reciente", "mas reciente", "investiga", "investígame", "investigame",
        ]
        # Regla: si hay verbo de búsqueda O petición de información,
        # Y hay indicador explícito de que el usuario quiere información de vuelta
        # → información para Tavily, no automation (browser).
        # Sin indicador de info, "busca X" cae al bloque de automation (DuckDuckGo).
        _has_search_verb = bool(_RE_SEARCH_VERB.search(user_input_lower))
        _has_info_request = bool(_RE_INFO_REQUEST.search(user_input_lower))
        _has_info_indicator = any(ind in user_input_lower for ind in _info_search_indicators)
        _has_site_kw = any(s in user_input_lower for s in site_keywords)
        _has_app_kw = bool(_RE_NATIVE_APPS.search(user_input_lower))

        # Guard: NO desviar a web_search si es un comando explícito de "entrar a [página
        # conocida] y extraer/investigar [tema]". Eso va al híbrido BROWSE_AND_EXTRACT
        # (página oficial PRIMERO, fallback web si no rinde). Sin este guard, indicadores
        # de info como "situación actual"/"y dime" robarían el comando antes del bloque
        # BROWSE_AND_EXTRACT de CAPA 1. El sitio capturado debe ser conocido o un dominio.
        _be_guard_m = _RE_BROWSE_EXTRACT.search(user_input_lower)
        _is_browse_extract_cmd = False
        if _be_guard_m:
            from core.app_config import WEB_APP_URLS as _WEB_URLS_G, looks_like_domain
            _be_site = _be_guard_m.group(1).strip()
            _is_browse_extract_cmd = _be_site in _WEB_URLS_G or looks_like_domain(_be_site)
        else:
            # Variante con verbo de búsqueda: "investiga en la página oficial de X
            # sobre Y" → también es BROWSE_AND_EXTRACT (bloque research-site de CAPA 1).
            _rs_guard_m = _RE_RESEARCH_SITE.search(user_input_lower)
            if _rs_guard_m:
                from core.app_config import (
                    WEB_APP_URLS as _WEB_URLS_G2, looks_like_domain as _is_domain_g2,
                )
                _rs_site = _rs_guard_m.group(1).strip()
                _rs_topic = _rs_guard_m.group(2).strip()
                _is_browse_extract_cmd = bool(_rs_topic) and (
                    _rs_site in _WEB_URLS_G2 or _is_domain_g2(_rs_site)
                )

        if (_has_search_verb or _has_info_request) and _has_info_indicator and not _is_browse_extract_cmd:
            return {
                "intent": "information",
                "action": "web_search",
                "query": user_input,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
                "needs_web_search": True,
            }

        # 1.2 Automatización con soporte contextual
        auto_keywords = list(AUTO_VERBS)  # fuente única: core/app_config.py
        navigation_triggers = ["ingresa a ", "ingresa en ", "entra a ", "entra en ", "ve a ", "ve al ", "ve a la ", "ir a ", "anda a ", "dirigete a ", "dirígete a ", "visita ", "navega a ", "abre la página de ", "abre la pagina de ", "abre la web de "]
        search_triggers = ["busca ", "buscar ", "encuentra ", "investiga ", "consulta ", "pon ", "reproduce "]
        # site_keywords y apps ya definidos arriba (reutilizados en el bloque de automation)

        # Lista combinada de sitios web reconocidos (keywords + dominios comunes)
        web_sites = site_keywords + list(_WEB_ALIASES.values())
        
        def is_web_site(query: str) -> bool:
            """Determina si un query parece ser un sitio web (no una app nativa)"""
            query_lower = query.lower()
            # 1. Está en web_sites (keywords conocidos)
            if any(site in query_lower for site in web_sites):
                return True
            # 2. Parece un dominio (contiene punto y no espacios)
            if "." in query_lower and " " not in query_lower:
                # Verificar que tenga TLD común
                tld_pattern = r'\.(com|net|org|io|ai|app|es|ar|mx|dev|info|edu|gov|uk|de|fr)$'
                if re.search(tld_pattern, query_lower):
                    return True
            return False

        def clean_query(fragment: Any) -> Optional[str]:
            if fragment is None:
                return None
            f_str: str = str(fragment).strip()
            if not f_str:
                return None
            candidate: str = f_str
            candidate = re.sub(r'^(?:y\s+)', '', candidate)
            candidate = re.sub(r'^(?:a\s+|al\s+|a la\s+|a los\s+|a las\s+|el\s+|la\s+|los\s+|las\s+)', '', candidate)
            
            # Explicitly type and str() every item to satisfy @-type inference issues
            combined_list = list(auto_keywords)
            for t in search_triggers:
                combined_list.append(str(t).strip())
            for p in navigation_triggers:
                combined_list.append(str(p).strip())
            combined_keywords = set(combined_list)
            
            for separator in [" y ", ",", ";", " luego ", " despues ", " después "]:
                c_low: str = str(candidate).lower()
                if str(separator) in c_low:
                    parts = str(candidate).split(str(separator), 1)
                    if len(parts) > 1:
                        trailing = str(parts[1]).lower()
                        if any(token and str(token) in trailing for token in combined_keywords):
                            candidate = str(parts[0])
                            break
            candidate = candidate.strip(" .,!?:;")
            return candidate or None

        def extract_search_query(original_text: str, lowered_text: str) -> Optional[str]:
            for trigger in search_triggers:
                if trigger in lowered_text:
                    # Case-insensitive split: trigger is lowercase but original may have caps
                    parts = re.split(re.escape(trigger), original_text, maxsplit=1, flags=re.IGNORECASE)
                    if len(parts) > 1:
                        fragment = parts[1]
                        cleaned = clean_query(fragment)
                        if cleaned:
                            return cleaned
            for phrase in navigation_triggers:
                if phrase in lowered_text:
                    parts = re.split(re.escape(phrase), original_text, maxsplit=1, flags=re.IGNORECASE)
                    if len(parts) > 1:
                        fragment = parts[1]
                        cleaned = clean_query(fragment)
                        if cleaned:
                            return cleaned
            url_match = re.search(r'(https?://\S+)', original_text)
            if url_match:
                return url_match.group(1).strip()
            domain_match = re.search(r'\b[\w\-]+\.(?:com|net|org|io|ai|app|es|ar|mx|dev)\b', lowered_text)
            if domain_match:
                return domain_match.group(0)
            # Use web_sites (superset of site_keywords) so twitter/discord/etc. are found.
            # Sort longest-first so "youtube music"/"prime video" win over bare "youtube"/"prime".
            for keyword in sorted(web_sites, key=len, reverse=True):
                if keyword in lowered_text:
                    return keyword
            # Alias check: single-word aliases with word boundaries (avoids "x" in "netflix").
            for alias, canonical in _WEB_ALIASES.items():
                if re.search(rf'\b{re.escape(alias)}\b', lowered_text):
                    return canonical
            # Last resort: extract up to 2 words right after an open verb.
            open_verb_match = re.search(
                r'\b(?:abre|abrir|open|launch|lanza)\s+([\w][\w\.\-]*(?:\s+\w+)?)',
                lowered_text
            )
            if open_verb_match:
                candidate = open_verb_match.group(1)
                # "source"/"ai" nunca son un target real — evita basura tipo
                # "source eran" cuando el "open" venía de "open source"/"open ai".
                if re.match(r'^(?:source|ai)\b', candidate):
                    return None
                return _WEB_ALIASES.get(candidate, candidate)
            return None

        # "open source" / "open ai" son sustantivos técnicos en boca hispana
        # ("un modelo open source"), no el verbo inglés "open" (abrir). Sin este
        # guard, mencionar "IA open source" disparaba automation → abría el
        # navegador default con un target basura ("msedge ghost bug"). Los
        # comandos reales ("open firefox") no llevan source/ai detrás.
        _auto_check_text = re.sub(r'\bopen\s+(?:source|ai)\b', ' ', user_input_lower)
        auto_word_hit = any(re.search(rf"\b{re.escape(word)}\b", _auto_check_text) for word in auto_keywords)
        # Peticiones indirectas/corteses: "necesito que abras X", "quiero que lances X",
        # "puedes abrir X", "me puedes iniciar X" → tratar como comandos de automation.
        _polite_request_hit = bool(re.search(
            rf'\b(?:necesito|quiero|quisiera|quisieras|me\s+gustar[ií]a|pod[eé]?s|pod[ií]as)\s+(?:que\s+)?'
            rf'(?:\w+\s+)?{AUTO_VERBS_PATTERN}\w*',
            user_input_lower
        ))
        if _polite_request_hit:
            auto_word_hit = True
        navigation_hit = any(phrase in user_input_lower for phrase in navigation_triggers)
        # MEJORADO: Usar word-boundary para apps (antes usaba 'in' que podía matchear substrings)
        app_hit = any(re.search(rf"\b{re.escape(app)}\b", user_input_lower) for app in apps)

        # Guard conversacional: si la frase es una queja, pregunta o referencia pasada
        # sobre una acción, NO es un comando de automation.
        # Ej: "por qué no pudiste abrir discord", "ya abriste discord en firefox"
        # NOTA: NO incluir "abierto/abierta" solos — "discord no está abierto" o
        # "quiero discord abierto" son comandos legítimos, no preguntas conversacionales.
        _conversational = bool(re.search(
            r'\b(?:'
            # Quejas, preguntas retóricas, referencias pasadas
            r'por\s*qu[eé]|c[oó]mo\s+es\s+que|cu[aá]ndo|si\s+eres|eres\s+capaz|'
            r'no\s+pudiste|no\s+puedes|ya\s+abriste|ahora\s+abriste|'
            # Quejas pasadas: "no (me/te) abriste/llegaste/conseguiste/pudiste/lanzaste/ejecutaste"
            # Cubre tanto "no abriste discord" como "no me abriste youtube" y "no llegaste a abrir nada".
            r'no\s+(?:me\s+|te\s+|le\s+|les\s+|nos\s+)?'
            r'(?:llegaste|abriste|conseguiste|pudiste|alcanzaste|lanzaste|ejecutaste|hiciste|iniciaste|cerraste)\b|'
            # "que no llegaste a / que no abriste / etc"
            r'que\s+no\s+(?:llegaste|abriste|pudiste|ejecutaste|conseguiste|lanzaste)\b|'
            r'no\s+te\s+ped[ií]|en\s+ning[uú]n\s+momento|nunca\s+te|'
            # "abriste/abrió/abrieron" = pasado 2da/3ra → conversacional. PERO
            # "abrí" pelado es imperativo voseo (comando) → NO lo capturamos acá.
            r'abr(?:iste|i[oó]|ieron)\b|ya\s+est[aá]\s+abierto|ya\s+lo\s+abriste|'
            # Otras formas de pasado 1ra persona (yo lanzé, ejecuté, inicié, cerré, etc.)
            r'lanc[eé]\b|ejecut[eé]\b|inici[eé]\b|cerr[eé]\b|activ[eé]\b|'
            # Quejas de que algo no funcionó (nunca es un comando, es un reporte pasado).
            # Ej: "abrí discord ayer y no funcionó" → conversacional, no automation.
            r'no\s+funcion[oóa]\w*|no\s+sirvi[oó]|no\s+anduvo|no\s+abri[oó]\b|'
            # Meta-preguntas sobre cómo Zephyro busca/filtra/analiza
            r'c[oó]mo\s+(?:buscas|filtras|encuentras|analizas|obtienes|sacas|seleccionas|verificas|validas|consigues)|'
            # Referencias posesivas a capacidades de Zephyro
            r'(?:tus|sus)\s+(?:fuentes|filtros|resultados|b[uú]squedas|criterios|m[eé]todos)|'
            # Preguntas de origen ("de dónde sacas...")
            r'de\s+d[oó]nde\s+(?:sacas|obtienes|buscas|consigues|sale[ns]?)|'
            # Negación explícita de acción (soporta pronombres: no me abras, no te lance, etc.)
            rf'no\s+(?:me\s+|te\s+|le\s+|les\s+|nos\s+|os\s+)?'
            rf'(?:{AUTO_VERBS_PATTERN}|navegues|navegar)\b|'
            # Frases explicativas / meta-discusión sobre el propio asistente
            r'es\s+que\b|en\s+tu\s+c[oó]digo|ya\s+est[aá]\s+configurado|'
            r'est[aá]\s+configurado\s+para|que\s+significar[ae]|te\s+estoy\s+diciendo|'
            r'estuve\s+diciendo|hab[ií]a\s+dicho|ya\s+te\s+dije|'
            # Relato de terceros ("escuché que X estaba diciendo...") — el usuario
            # cuenta algo, no ordena; sin esto un verbo suelto en la cita
            # disparaba automation.
            r'estaba\s+diciendo|escuch[eé]\s+(?:que|sobre)|dijo\s+que|dec[ií]a\s+que|'
            r'mencion[oó]\s+que|coment[oó]\s+que|cont[oó]\s+que'
            r')\b',
            user_input_lower
        ))

        # Guard cláusulas subordinadas: si el verbo de acción aparece dentro de una
        # cláusula subordinada ("para que puedas abrir", "capaz de abrir apps",
        # "pienso darte una skill para abrir"), NO es un comando.
        _subordinate = bool(re.search(
            r'\b(?:'
            r'para\s+(?:que\s+)?(?:puedas|pueda|poder)|'
            r'capaz\s+de|'
            r'idea\s+de|la\s+idea\s+es|'
            r'pienso|planeo|planeando|'
            r'por\s+comodidad|'
            r'asegurarme|'
            r'seas\s+capaz|'
            r'que\s+(?:puedas|pueda|sepas|sepa)|'
            rf'para\s+{AUTO_VERBS_PATTERN}|'
            rf'poder\s+{AUTO_VERBS_PATTERN}|'
            r'habilidad\s+(?:de|para)|'
            r'skill\s+(?:de|para)|'
            r'funcionalidad\s+(?:de|para)'
            rf')\s+.*\b{AUTO_VERBS_PATTERN}',
            user_input_lower
        ))

        if _conversational or _subordinate:
            logger.debug(
                "[router] guard bloqueó automation: %s — input=%r",
                "subordinate" if _subordinate else "conversational",
                user_input[:100],
            )
            auto_word_hit = False
            app_hit = False
            # Short-circuit to information to prevent CAPA 2 (Haiku) from
            # overriding the guard and misclassifying the phrase as automation.
            guard_label = "subordinate_clause_detected" if _subordinate else "conversational_guard"
            return {
                "intent": "information",
                "action": "respond",
                "query": user_input,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
                "_guard": guard_label,
            }

        # app_hit sola NO es suficiente — mencionar "discord" sin verbo de comando
        # (ej. "discord es genial", "me gusta discord") no debe disparar automation.
        # Se requiere auto_word_hit o navigation_hit para activar automation.

        # ── BROWSE_AND_EXTRACT: "entra a [URL/sitio] y dime/extrae/busca [query]" ──
        # Detecta cuando el usuario quiere que Zephyro navegue a una página específica
        # y extraiga información (ej: "entra a onpe.gob.pe y dime los resultados")
        if navigation_hit and not _conversational and not _subordinate:
            _browse_extract_re = _RE_BROWSE_EXTRACT.search(user_input_lower)
            if _browse_extract_re:
                _browse_target = _browse_extract_re.group(1).strip()
                _extract_query = _browse_extract_re.group(2).strip()
                # Resolver URL si es un sitio conocido
                from core.app_config import (
                    WEB_APP_URLS as _WEB_URLS, looks_like_domain as _is_domain_be,
                    normalize_target as _norm_be,
                )
                _browse_target = _norm_be(_browse_target)
                _browse_url = _WEB_URLS.get(_browse_target)
                if not _browse_url:
                    # Dominio real (con TLD), no "cualquier cosa con un punto"
                    if _is_domain_be(_browse_target):
                        _browse_url = f"https://{_browse_target}"
                    else:
                        _browse_url = None
                if _browse_url:
                    self._last_automation_turn = self._turn_counter
                    return {
                        "intent": "automation",
                        "automation_intent": "BROWSE_AND_EXTRACT",
                        "action": "browse_extract",
                        "target": _browse_target,
                        "url": _browse_url,
                        "extract_query": _extract_query,
                        "complexity": complexity_info["label"],
                        "complexity_score": complexity_info["probability"],
                        "_source": "capa1_browse_extract",
                    }

        # ── Research-on-named-site: "investiga en la página oficial de [sitio] sobre [tema]" ──
        # Cubre el caso donde el verbo es de búsqueda (no de navegación) → navigation_hit=False
        # y el bloque BROWSE_AND_EXTRACT de arriba no aplica. Tolera el typo STT "invstiga".
        # Requiere un sitio CONOCIDO (en WEB_APP_URLS) + una cláusula de tema explícita,
        # para no disparar con menciones conversacionales ("mira la página de facebook").
        if not _conversational and not _subordinate:
            _research_site_re = _RE_RESEARCH_SITE.search(user_input_lower)
            if _research_site_re:
                _r_site = _research_site_re.group(1).strip()
                _r_topic = _research_site_re.group(2).strip()
                from core.app_config import (
                    WEB_APP_URLS as _WEB_URLS_R, looks_like_domain as _is_domain_rs,
                    normalize_target as _norm_rs,
                )
                _r_site = _norm_rs(_r_site)
                _r_url = _WEB_URLS_R.get(_r_site)
                if not _r_url and _is_domain_rs(_r_site):
                    _r_url = f"https://{_r_site}"
                _r_known = _r_site in _WEB_URLS_R or _is_domain_rs(_r_site)
                if _r_url and _r_known and _r_topic:
                    self._last_automation_turn = self._turn_counter
                    return {
                        "intent": "automation",
                        "automation_intent": "BROWSE_AND_EXTRACT",
                        "action": "browse_extract",
                        "target": _r_site,
                        "url": _r_url,
                        "extract_query": _r_topic,
                        "complexity": complexity_info["label"],
                        "complexity_score": complexity_info["probability"],
                        "_source": "capa1_research_site",
                    }

        # ── Sequence pattern guard (calculado antes de TODO el bloque CAPA 1) ──
        # Si el input tiene un conector "y luego/después" seguido de OTRO
        # verbo de automation, saltamos toda la lógica de CAPA 1 (media,
        # compound, extract_search_query) y dejamos que CAPA 1.5 (LLM
        # extractor) lo procese como sequence multi-step (devuelve
        # action="sequence", steps=[...]).
        _SEQUENCE_HINT_RE = re.compile(
            rf'\b(?:y\s+luego|y\s+despu[eé]s|despu[eé]s\s+de|despu[eé]s|luego)\s+'
            rf'(?:{AUTO_VERBS_PATTERN}|y\s+(?:{AUTO_VERBS_PATTERN}))\b',
            re.IGNORECASE,
        )
        _has_sequence_hint = bool(_SEQUENCE_HINT_RE.search(user_input_lower))

        if (auto_word_hit or navigation_hit) and not _has_sequence_hint:
            action_value = "open"
            if re.search(r'\b(cierra|cerrar|termina|finaliza|mata|kill)\b', user_input_lower):
                action_value = "close"
            elif re.search(r'\b(actualiza|update)\b', user_input_lower):
                action_value = "update"

            target = _resolve_app_target(user_input_lower, apps)

            alias_map = dict(_CFG_APP_ALIASES)  # from centralized config
            alias_map["explorador"] = "dolphin"  # legacy alias
            target = alias_map.get(target, target)
            # Apply web aliases to target (e.g. "x" → "twitter")
            target = _WEB_ALIASES.get(target, target)

            # ── Media entity extraction (order-independent) ──────────────────
            # Runs before the compound parser so "abre youtube y reproduce X"
            # is resolved as PLAY_MEDIA instead of falling through to NAVIGATE.
            # PERO: si hay sequence hint, saltamos esto para que el LLM
            # extractor procese el input completo como sequence.
            _media = None if (_has_sequence_hint or navigation_hit) else _extract_media_entities(user_input)
            # Contrato anti-alucinación: un intent de media detectado SIEMPRE
            # va a automation (provider default si no se especificó plataforma).
            if _media:
                self._last_automation_turn = self._turn_counter
                return {
                    "intent": "automation",
                    "automation_intent": "PLAY_MEDIA",
                    "provider": _media["provider"],
                    "media_type": _media.get("media_type", "song"),
                    "action": "open",
                    "target": _media["provider"],
                    "search_query": _media["search_query"],
                    "album": _media.get("album"),
                    "artist": _media.get("artist"),
                    "complexity": complexity_info["label"],
                    "complexity_score": complexity_info["probability"],
                    "_source": "capa1_media_entities",
                }

            # ── Nav-subpath: "entra a {section} de {site}" ─────────────────
            # "Entra a Reels de Instagram", "Ve a Trending de Twitter", etc.
            # Runs before compound parser so a missing "y" connector doesn't
            # silently fall through to the LLM fast-lane.
            #
            # El verbo de APERTURA también cuenta ("abre Reels de Instagram"):
            # sin esto, navigation_hit era False (navigation_triggers solo tiene
            # "abre la página de ") y el sub_path se perdía → se abría la home
            # del sitio en vez de la sección. NO se toca navigation_hit, que
            # gatea otros bloques; el regex de abajo ya exige "{section} de
            # {sitio conocido}", así que "abre spotify" no puede matchear.
            _open_verb_subpath = bool(re.search(
                r'\b(?:abre|abr[eií]me|ábreme|abrir|abr[ií])\b.+\s+de\s+\w+\s*$',
                user_input_lower,
            ))
            if (navigation_hit or _open_verb_subpath) and not _has_sequence_hint:
                _nav_subpath_re = re.search(
                    r'(?:entra?\s+(?:a\s+|en\s+)?|ve\s+a\s+|navega\s+a\s+|ir\s+a\s+|visita\s+'
                    r'|abre\s+|abr[eií]me\s+|ábreme\s+|abrir\s+|abr[ií]\s+)\s*'
                    r'(?:(?:la\s+)?secci[oó]n\s+(?:de\s+)?|el\s+)?'
                    r'(.+?)\s+'
                    r'(?:de\s+la\s+|de\s+|en\s+la\s+|en\s+)\s*'
                    r'([\w]+)\s*$',
                    user_input_lower,
                )
                if _nav_subpath_re:
                    _section = _nav_subpath_re.group(1).strip()
                    _raw_site = _nav_subpath_re.group(2).strip()
                    _site = _WEB_ALIASES.get(_raw_site, _raw_site)
                    # "abre la página de instagram" pide la HOME, no una sección:
                    # sin este guard el sub_path quedaba "la pagina" y se navegaba
                    # a instagram.com/la pagina (404).
                    if _section in _GENERIC_HOME_SECTIONS:
                        _section = ""
                    # The open verb is broader than "entra a": "abre el perfil de
                    # instagram", "abre un video de youtube", "abre mis mensajes de
                    # whatsapp" turned arbitrary phrases into sub-paths (404s). Named
                    # sections come bare and as one word ("abre reels de instagram"),
                    # so with the open verb alone require exactly that.
                    if _section and _open_verb_subpath and not navigation_hit:
                        _has_determiner = re.search(
                            r'\b(?:abre|abr[eií]me|ábreme|abrir|abr[ií])\s+'
                            r'(?:el|la|los|las|un|una|unos|unas|mi|mis|tu|tus|su|sus)\s',
                            user_input_lower,
                        )
                        if _has_determiner or not re.fullmatch(r'\w+', _section):
                            _section = ""
                    if _section and (_site in web_sites or _site in apps or _WEB_ALIASES.get(_site)):
                        self._last_automation_turn = self._turn_counter
                        return {
                            "intent": "automation",
                            "automation_intent": "NAVIGATE",
                            "action": "open",
                            "target": _site,
                            "sub_path": _section,
                            "complexity": complexity_info["label"],
                            "complexity_score": complexity_info["probability"],
                            "_source": "capa1_nav_subpath",
                        }

            # ── Nav-subpath contextual: "entra a [section]" con sitio implícito ──
            # Handles follow-up commands when the site comes from session context:
            # "entra a Reels" after opening Instagram → navigate to instagram.com/reels
            if navigation_hit and not _has_sequence_hint and last_browser_target:
                _bare_nav_re = re.search(
                    r'(?:entra?\s+(?:a\s+|en\s+)?|ve\s+a\s+|navega\s+a\s+|ir\s+a\s+|visita\s+)'
                    r'(?:(?:la\s+)?secci[oó]n\s+(?:de\s+)?|el\s+)?'
                    r'([\w][^.!?]*?)\s*$',
                    user_input_lower,
                )
                if _bare_nav_re:
                    _section = _bare_nav_re.group(1).strip()
                    # Guard: si la section es en sí misma un sitio conocido,
                    # no forzar jerarquía (e.g. "entra a youtube" no es sub-nav de instagram)
                    _is_known_site = _section in web_sites or _section in apps
                    if _section and len(_section.split()) <= 3 and not _is_known_site:
                        self._last_automation_turn = self._turn_counter
                        return {
                            "intent": "automation",
                            "automation_intent": "NAVIGATE",
                            "action": "open",
                            "target": last_browser_target,
                            "sub_path": _section,
                            "complexity": complexity_info["label"],
                            "complexity_score": complexity_info["probability"],
                            "_source": "capa1_nav_subpath_context",
                        }

            # ── Compound command: "abre [app] y [acción]" ───────────────────
            # Detect patterns like "abre instagram y entra a reels" or
            # "abre youtube y busca lo-fi music" BEFORE calling extract_search_query
            # so that the secondary action doesn't clobber the primary app target.
            # Strip "en [el] [navegador/firefox/…]" so "abre instagram en el navegador y …"
            # matches the same as "abre instagram y …".
            _input_for_compound = re.sub(
                r'\s+en\s+(?:el\s+)?(?:navegador|firefox|chrome|chromium|edge|msedge|browser|web)',
                '',
                user_input_lower
            )

            # Si hay sequence hint ("y luego") con verbo media, dejamos que el
            # LLM extractor (CAPA 1.5) lo procese como sequence multi-step en
            # vez de que el compound parser arme actions[] potencialmente mal.
            _compound_re = None
            if not _has_sequence_hint:
                # Conectores compuestos: "y", "y luego", "y después", etc.
                _COMPOUND_CONNECTORS = r"(?:y\s+luego|y\s+despu[eé]s|y|luego|despu[eé]s)"
                _compound_re = re.search(
                    rf'\b{AUTO_VERBS_PATTERN}\s+(.+?)\s+{_COMPOUND_CONNECTORS}\s+({AUTO_VERBS_PATTERN}\s+)?(.+)',
                    _input_for_compound
                )
            if _compound_re:
                _primary_raw = _compound_re.group(1).strip()
                # group(2) is the optional verb prefix (e.g. "busca "); group(3) is the rest.
                # Reconstruct so _secondary_raw = "busca lo-fi music", not just "lo-fi music".
                _verb_opt = _compound_re.group(2) or ""
                _secondary_raw = (_verb_opt + _compound_re.group(3)).strip()
                _primary_app = _WEB_ALIASES.get(_primary_raw.lower(), _primary_raw.lower())

                # Build action list: determine what kind of secondary action this is
                _actions: list[Dict[str, Any]] = []
                _actions.append({"action": "open", "target": _primary_app})

                # Check if secondary is a navigation/sub-path on the primary site
                _nav_m = re.match(
                    r'(?:entra?\s+(?:a\s*|en\s*)?|ve\s+a(?:l\s+)?|navega\s+a\s+|visita\s+|ir\s+a\s+|anda\s+a\s+)\s*(.+)',
                    _secondary_raw
                )
                _srch_m = re.match(
                    rf'(?:{AUTO_VERBS_PATTERN}\s+)?busca\s+(.+)',
                    _secondary_raw
                )
                # Check if secondary is another independent app/site
                _sec_clean = re.sub(
                    rf'^(?:{AUTO_VERBS_PATTERN})\s+', '', _secondary_raw
                ).strip()
                _sec_alias = _WEB_ALIASES.get(_sec_clean.lower(), _sec_clean.lower())

                if _nav_m:
                    _sub_path = _nav_m.group(1).strip()
                    _actions[-1]["sub_path"] = _sub_path
                elif _srch_m:
                    _sq = _srch_m.group(1).strip()
                    _tmpl = _SITE_SEARCH_URLS.get(_primary_app)
                    if _tmpl:
                        _actions[-1]["navigate_target"] = _tmpl.format(q=quote_plus(_sq))
                    else:
                        _actions[-1]["sub_path"] = f"search?q={quote_plus(_sq)}"
                elif _sec_alias in web_sites or is_web_site(_sec_alias) or _sec_alias in apps:
                    # Second clause is an independent app/site → MULTI_ACTION
                    _actions.append({"action": "open", "target": _sec_alias})
                else:
                    # Unknown second target — include as unknown for honest reporting
                    _actions.append({"action": "unknown", "target": _sec_clean})

                _automation_intent = "MULTI_ACTION" if len(_actions) > 1 else "NAVIGATE"

                _compound_analysis: Dict[str, Any] = {
                    "intent": "automation",
                    "automation_intent": _automation_intent,
                    "actions": _actions,
                    "action": "open",
                    "target": _primary_app,
                    "complexity": complexity_info["label"],
                    "complexity_score": complexity_info["probability"],
                    "_source": "capa1_compound",
                }
                # Backward compat: carry navigate_target / sub_path on the top level too
                if len(_actions) == 1 and _actions[0].get("navigate_target"):
                    _compound_analysis["navigate_target"] = _actions[0]["navigate_target"]
                elif len(_actions) == 1 and _actions[0].get("sub_path"):
                    _compound_analysis["sub_path"] = _actions[0]["sub_path"]
                self._last_automation_turn = self._turn_counter
                return _compound_analysis
            # ────────────────────────────────────────────────────────────────

            search_query = extract_search_query(user_input, user_input_lower)
            # Apply web aliases to extracted search_query as well
            if search_query:
                search_query = _WEB_ALIASES.get(search_query.lower().strip(), search_query)
            # Strip provider suffix ("en youtube", "en spotify", etc.)
            # so "gold in the ceiling en youtube" → "gold in the ceiling"
            if search_query:
                _prov_match = re.search(
                    r'(?:^|\s)en\s+(youtu(?:be|b|be\s+music)?|yotube|spotify|google|firefox|edge|el\s+navegador)\s*$',
                    search_query, re.IGNORECASE
                )
                if _prov_match:
                    _stripped = search_query[:_prov_match.start()].strip()
                    if _stripped:
                        search_query = _stripped
            used_context = False
            navigate_target = None
            browsers = ["firefox", "browser", "navegador", "chrome", "edge", "msedge"]
            has_search_trigger = any(trigger in user_input_lower for trigger in search_triggers)
            
            if search_query:
                is_website = is_web_site(search_query)
                
                # Caso 1: Target es navegador y search_query es sitio web → navegar al sitio
                if target in browsers and is_website:
                    navigate_target = search_query
                    search_query = None
                # Caso 2: Target desconocido y search_query es sitio web → navegar al sitio
                elif target == "unknown" and is_website:
                    target = search_query
                    search_query = None  # Ya no es una búsqueda, es el destino
                # Caso 3: Hay trigger de búsqueda explícito → tratar como búsqueda
                elif has_search_trigger:
                    # search_query ya es el término de búsqueda
                    if target == "unknown":
                        if last_browser_target:
                            target = last_browser_target
                            used_context = True
                        else:
                            target = self.default_browser
                    elif target in {"browser", "navegador"}:
                        if last_browser_target:
                            target = last_browser_target
                            used_context = True
                        else:
                            target = self.default_browser
                # Caso 4: search_query es sitio web sin trigger de búsqueda (ya manejado arriba)
                # Caso 5: search_query no es sitio web → búsqueda genérica
                else:
                    if target == "unknown":
                        if last_browser_target:
                            target = last_browser_target
                            used_context = True
                        else:
                            target = self.default_browser
                    elif target in {"browser", "navegador"}:
                        if last_browser_target:
                            target = last_browser_target
                            used_context = True
                        else:
                            target = self.default_browser
            # Si no hay search_query pero target es unknown, no hacemos nada (se maneja después)

            if action_value == "close" and target == "unknown" and isinstance(last_payload, dict):
                candidate = last_payload.get("target")
                if isinstance(candidate, str):
                    target = candidate
                    used_context = True

            if target == "unknown" and not search_query:
                pass
            else:
                # Detectar automation_intent y provider
                # Primero verificar si es una apertura simple de app nativa
                is_simple_native_app_open = (
                    action_value == "open" 
                    and target in apps 
                    and not search_query 
                    and not navigate_target
                )
                
                if is_simple_native_app_open:
                    automation_intent = None
                else:
                    automation_intent = "NAVIGATE"
                provider = "generic"
                
                # Análisis heurístico del texto del usuario
                # Misma normalizacion de voseo que arriba: estas dos
                # re-derivaciones la perdian.
                user_input_lower = _verbs.normalize_voseo(user_input.lower())
                
                # Detectar intención de reproducción. Se delega a
                # `_extract_media_entities`, el MISMO detector que usa el camino
                # principal de CAPA 1.
                #
                # Antes había acá una SEGUNDA lista de keywords
                # (`playback_keywords`) matcheada por substring y sin exigir
                # objeto de media: "ver" matcheaba dentro de "universidad" y
                # "diversos", "mira" dentro de "admirable", y "pon" dentro de
                # "pon la fecha del parcial en el informe" — que así terminaba
                # reproduciendo música (medido el 2026-10-01). Peor: cuando se
                # llega a este punto el detector cuidadoso YA corrió y dijo que
                # no era media, y esta lista le pisaba la decisión.
                _media_hint = _extract_media_entities(user_input)
                if _media_hint:
                    automation_intent = "PLAY_MEDIA"
                    # Detectar proveedor. Word-boundary: "yt" es substring de
                    # "python" y de "myth".
                    if re.search(r"\b(?:youtube music|yt music)\b", user_input_lower):
                        provider = "youtube_music"
                    elif re.search(r"\b(?:youtube|yt)\b", user_input_lower):
                        provider = "youtube"
                
                # Detectar búsqueda (solo si no hay navigate_target definido)
                search_keywords = ["busca", "buscar", "encuentra", "encuentre", "búscame"]
                if not navigate_target and any(kw in user_input_lower for kw in search_keywords):
                    automation_intent = "SEARCH"
                
                # Detectar interacción específica
                interact_keywords = ["clic", "click", "presiona", "pulsa", "escribe", "teclea", "selecciona"]
                if any(kw in user_input_lower for kw in interact_keywords):
                    automation_intent = "INTERACT"
                
                # Detectar cierre. EL TARGET EXPLÍCITO GANA.
                #
                # NOTA: word-boundary para evitar falsos positivos —
                # "termina" es substring de "terminal" ("abre terminal").
                #
                # Antes este bloque reescribía TODO verbo de cierre a
                # `INTERACT/close_tab`, pisando el `action_value="close"` que
                # se calculó ~340 líneas arriba. Dos consecuencias medidas el
                # 2026-10-02:
                #   1. "cierra word" → close_tab → el ejecutor
                #      (`app_controller.py:542`) hace
                #      `return await self.browser.close_tab()` e IGNORA el
                #      target: cerraba una pestaña del browser, no Word.
                #   2. `_classify_close` del guardrail evalúa `action == "close"`,
                #      así que NUNCA se alcanzaba desde el camino regex — con
                #      su lista de procesos protegidos y de apps con trabajo
                #      sin guardar incluidas.
                #
                # Ahora `close_tab` se emite solo cuando el cierre es de una
                # PESTAÑA: hay sustantivo de pestaña, o el target es el
                # navegador (el caso "cerrá la pestaña" después de navegar).
                if re.search(r'\b(cierra|cerrar|cierre|termina|finaliza)\b', user_input_lower):
                    _tab_noun = re.search(
                        r'\b(pesta[nñ]a|pestanas|pesta[nñ]as|tab|tabs|solapa'
                        r'|ventana\s+del?\s+navegador)\b',
                        user_input_lower,
                    )
                    # Un NAVEGADOR o un SITIO WEB se cierran como pestaña; una
                    # app nativa, como proceso. "terminá youtube" es una
                    # pestaña — intentar matar un proceso llamado "youtube" no
                    # tiene sentido — y "cerrá word" es un proceso.
                    _browser = target in {
                        "firefox", "chrome", "msedge", "edge", "brave", "opera",
                        "vivaldi", "chromium", "navegador", "browser",
                    }
                    # Si el target es una app nativa, gana la app: "spotify"
                    # está en NATIVE_APPS y además en SITE_KEYWORDS, y
                    # "cerrá spotify" se refiere al programa.
                    _site_only = (
                        target not in apps
                        and (target in site_keywords or target in web_sites)
                    )
                    if _tab_noun or _browser or _site_only:
                        automation_intent = "INTERACT"
                        action_value = "close_tab"
                    else:
                        # Cierre de app: queda en `action="close"`, que es lo
                        # que despacha `close_app` y lo que el guardrail sabe
                        # clasificar.
                        action_value = "close"
                
                analysis = {
                    "intent": "automation",
                    "automation_intent": automation_intent,
                    "provider": provider,
                    "action": action_value,
                    "target": target,
                    "complexity": complexity_info["label"],
                    "complexity_score": complexity_info["probability"],
                }
                if search_query:
                    analysis["search_query"] = search_query
                if navigate_target:
                    analysis["navigate_target"] = navigate_target
                if used_context:
                    analysis["used_previous_context"] = True
                # CAPA 1 heurística: confianza alta, no requiere confirmación
                self._last_automation_turn = self._turn_counter
                return analysis

        # --- CAPA 1.5: INFORMATION FAST-PATH ---
        # Queries con palabras interrogativas sin pistas de automatización ni memoria
        # retornan "information" directamente, sin pagar el costo del LLM clasificador.
        _info_question_patterns = [
            r'\b(quién|quien|qué|que|cuándo|cuando|dónde|donde|por qué|por que|cómo|como|cuál|cual|cuánto|cuanto)\b',
            r'\b(háblame|hablame|cuéntame|cuentame|dime sobre|dime más|dime mas)\b',
            r'\b(es verdad|es cierto|sabes|conoces|hay algún|hay algun)\b',
        ]
        _memory_cues = [
            "recuerdas", "me dijiste", "qué te dije", "que te dije",
            "cuando hablamos", "anteriormente me", "antes me dijiste",
        ]
        has_memory_cue = any(cue in user_input_lower for cue in _memory_cues)

        if not has_memory_cue and any(re.search(p, user_input_lower) for p in _info_question_patterns):
            return {
                "intent": "information",
                "action": "respond",
                "query": user_input,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
            }

        # --- CAPA 1.8: FILTRO CONVERSACIONAL (Pre-CAPA 2) ---
        # Si el mensaje es largo (>10 palabras) y NO contiene verbos de acción,
        # es casi seguro conversación/información → no invocar LLM clasificador.
        # Derivado de la fuente única core/verbs.py — antes era una lista LOCAL
        # en tuteo que NO heredaba de AUTO_VERBS (whack-a-mole: el voseo faltaba).
        _action_verbs = [
            r'\b' + _verbs.verbs_pattern("automation", "media", "search") + r'\b',
            r'\b(?:kill|start)\b',  # inglés extra que no está en los dominios
        ]
        _conversational_markers = [
            "en cuanto", "con respecto", "sobre el tema", "puede ser",
            "considero", "considerando", "riesgoso", "riesgo", "seguridad",
            "peligroso", "cuidado", "preocupa", "opino", "creo que",
            "me parece", "en mi opinión", "en mi opinion", "la verdad",
            "honestamente", "francamente", "sinceramente", "básicamente",
            "pienso", "planeo", "la idea es", "la idea de",
            "por comodidad", "asegurarme", "para que puedas", "seas capaz",
            "por el tema", "al respecto", "en relación", "en relacion",
            "el problema", "la cuestión", "la cuestion", "es importante",
            "no estoy seguro", "no sé si", "no se si", "podría", "podria",
            "debería", "deberia", "sería", "seria", "aunque", "sin embargo",
            "por otro lado", "de hecho", "en realidad", "a decir verdad",
            "entiendo que", "comprendo", "de acuerdo", "tienes razón",
            "tienes razon", "exactamente", "efectivamente", "correcto",
            # Frases explicativas / meta-discusión
            "es que", "en tu código", "en mi código", "ya está configurado",
            "te estoy diciendo", "estuve diciendo", "ya te dije",
        ]
        has_action_verb = any(re.search(p, user_input_lower) for p in _action_verbs)
        has_conversational_marker = any(marker in user_input_lower for marker in _conversational_markers)

        if word_count > 10 and not has_action_verb:
            # Mensaje largo sin verbos de acción → información directa
            return {
                "intent": "information",
                "action": "respond",
                "query": user_input,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
                "_guard": "conversational_filter_no_action_verbs",
            }

        if has_conversational_marker and not has_action_verb:
            # Marcadores conversacionales sin acción → información
            return {
                "intent": "information",
                "action": "respond",
                "query": user_input,
                "complexity": complexity_info["label"],
                "complexity_score": complexity_info["probability"],
                "_guard": "conversational_marker_detected",
            }

        # --- CAPA 1.9: GUARD ANTI-FALSO-POSITIVO (Pre-CAPA 2) ---
        # Si el mensaje menciona una app conocida pero NO tiene verbo de acción,
        # es conversacional (ej. "hablamos por discord", "me gusta spotify").
        # Excepción: si hay un trigger de petición cortés ("necesito que", "quiero que",
        # "puedes"), dejar pasar a CAPA 2 — son casos ambiguos que Haiku debe resolver.
        _polite_trigger = bool(re.search(
            r'\b(?:necesito|quiero|quisiera|quisieras|me\s+gustar[ií]a|pod[eé]?s|pod[ií]as)\s+(?:que\s+)?',
            user_input_lower
        ))
        if not has_action_verb:
            _all_known_apps = set(apps) | set(site_keywords) | set(web_sites)
            _mentions_app = any(app in user_input_lower for app in _all_known_apps)
            if _mentions_app and not _polite_trigger:
                return {
                    "intent": "information",
                    "action": "respond",
                    "query": user_input,
                    "complexity": complexity_info["label"],
                    "complexity_score": complexity_info["probability"],
                    "_guard": "app_mention_without_action_verb",
                }

        # ── CAPA 1.5: LLM Browser Intent Extractor ────────────────────────
        # Si llegamos aquí, los regex de Layer 1 no encontraron una acción
        # de automatización, pero el input podría ser un comando de navegador
        # con phrasing que los regex no cubren. Usar Haiku con structured JSON.
        # Solo se activa si el input parece un comando (tiene verbo de acción,
        # trigger de petición cortés, o keywords de media/automation sueltas)
        # y no es puramente conversacional.
        _has_media_kw = any(
            kw in user_input_lower
            for kw in ("produce", "toca", "reproduce", "álbum", "album", "canción",
                        "cancion", "artista", "playlist", "música", "musica",
                        "video", "play ", "dale a", "navega", "abre")
        )
        if has_action_verb or _polite_trigger or _has_media_kw or _has_sequence_hint:
            cache_key = user_input_lower.strip()
            if cache_key in self._browser_intent_cache:
                return dict(self._browser_intent_cache[cache_key])

            if not skip_llm:
                try:
                    extracted = await extract_browser_intent(
                        user_input, self.llm
                    )
                    if extracted is not None:
                        extracted["complexity"] = complexity_info.get("label", "simple")
                        extracted["complexity_score"] = complexity_info.get("probability", 0.5)
                        self._browser_intent_cache[cache_key] = dict(extracted)
                        if len(self._browser_intent_cache) > 200:
                            self._browser_intent_cache.pop(next(iter(self._browser_intent_cache)))
                        return extracted
                except Exception:
                    pass  # LLM falló, continuar con capa 2

        # ── CAPA 2: Haiku (Para casos genuinamente ambiguos — automatización compleja) ──

        # Fallback for skip_llm + sequence: CAPA 1.5 (LLM) is unavailable in cloud mode,
        # so extract the LAST automation target from "abre X y luego abre Y" manually.
        if skip_llm and _has_sequence_hint:
            _seq_parts = re.split(
                r'\b(?:y\s+luego|y\s+despu[eé]s|despu[eé]s\s+de|despu[eé]s|luego)\b',
                user_input_lower,
            )
            _last_step = _seq_parts[-1].strip() if len(_seq_parts) > 1 else ""
            _seq_target: Optional[str] = None
            # Longest match first so "youtube music" wins over bare "youtube"
            for _sk in sorted(site_keywords, key=len, reverse=True):
                if _sk in _last_step:
                    _seq_target = _sk
                    break
            if _seq_target is None:
                for _ap in sorted(apps, key=len, reverse=True):
                    if _ap in _last_step:
                        _seq_target = _ap
                        break
            if _seq_target:
                self._last_automation_turn = self._turn_counter
                return {
                    "intent": "automation",
                    "action": "open",
                    "target": _seq_target,
                    "complexity": complexity_info.get("label", "simple"),
                    "complexity_score": complexity_info.get("probability", 0.5),
                    "_source": "capa1_sequence_skip_llm",
                }

        if skip_llm:
            return {"intent": "information", "query": user_input, "complexity": complexity_info.get("label", "simple"), "complexity_score": complexity_info.get("probability", 0.5)}
        # Los dos guards de abajo se evalúan ANTES de llamar al modelo.
        # CAPA 2 solo puede devolver algo distinto de information/respond si la
        # etiqueta de Haiku es "command" Y ADEMÁS pasa esos dos guards. Si cualquiera
        # de los dos ya falla, el resultado es information/respond PASE LO QUE PASE:
        # la llamada al modelo es un round-trip cuya respuesta se descarta por
        # construcción. Medido 2026-10-04 sobre 100 utterances reales: 76 nunca llegan
        # acá, y de las 24 que llegan, 20 caen en este caso. Una llamada a Haiku que
        # no puede conectarse cuesta ~3s antes de rendirse.
        _table_worthy = bool(_verbs.TABLE_WORTHY_PATTERN.search(user_input_lower))
        _has_auto_verb = re.search(rf'\b{AUTO_VERBS_PATTERN}\b', user_input_lower) is not None
        if _table_worthy or not _has_auto_verb:
            return {
                "intent": "information",
                "action": "respond",
                "target": "",
                "confidence": 0.9,
                "_source": "capa2_skipped_no_effect",
                "complexity": complexity_info.get("label", "simple"),
                "complexity_score": complexity_info.get("probability", 0.5),
            }
        try:
            haiku_intent = await detect_intent(user_input)
            # Mapear intents semánticos → routing intents.
            # GUARD: 'command' de Haiku significa "frase imperativa", NO "comando
            # de sistema" — "Compará Java y JavaScript en una tabla" es chat.
            # Llegar a CAPA 2 implica que CAPA 1 (apps/sitios/verbos) no encontró
            # nada ejecutable, y un automation con action="execute" y target
            # vacío muere siempre en app_controller ("No estoy seguro de como
            # ejecutar esa accion"). El safety-net de automation se mantiene solo
            # si hay un verbo de acción real, y nunca para pedidos de
            # comparación/tabla.
            if (
                haiku_intent == "command"
                and not _table_worthy
                and _has_auto_verb
            ):
                intent = "automation"
                action = "execute"
                confidence = 0.75
            else:
                intent = "information"
                action = "respond"
                confidence = 0.9
            return {
                "intent": intent,
                "action": action,
                "target": "",
                "confidence": confidence,
                "_source": "capa2_haiku",
                "complexity": complexity_info.get("label", "simple"),
                "complexity_score": complexity_info.get("probability", 0.5),
            }
        except Exception:
            return {"intent": "information", "query": user_input, "complexity": complexity_info.get("label", "simple"), "complexity_score": complexity_info.get("probability", 0.5)}