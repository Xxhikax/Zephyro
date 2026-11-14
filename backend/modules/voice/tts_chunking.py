"""
Utilidades de fragmentación de texto para streaming TTS.

Convierten un stream de tokens (o un texto completo) en oraciones listas para
sintetizar una por una, replicando la lógica de acumulación de
`_generate_with_streaming_tts` (orchestrator.py) en un módulo puro y testeable.

Esta es la semilla de la Fase 1 de P4 (streaming TTS). En la Fase 0 sólo la usa
el benchmark de medición; NO está cableada al pipeline todavía.

Diseño:
- Funciones puras (sin asyncio, sin TTS, sin I/O) → fáciles de testear y medir.
- `clean` es un callback opcional de sanitización aplicado POR FRAGMENTO. Cortar
  en límites de oración es seguro para sanitizar por chunk (a diferencia de
  cortar a mitad de un `**bold**`, que partiría el markdown entre fragmentos).
"""
from __future__ import annotations

from typing import Callable, Iterable, Iterator, Optional

# Final de oración: mismo conjunto que `_generate_with_streaming_tts`.
SENTENCE_ENDS: frozenset[str] = frozenset({'.', '!', '?', '\n'})

# Si el buffer supera este nº de palabras sin cerrar oración, se descarga igual
# (cortando en una coma/punto y coma si hay, si no en el último espacio).
# Es un FALLBACK para oraciones largas; con texto puntuado normal las oraciones
# cierran en SENTENCE_ENDS antes. Era 5 y contaba TOKENS de subpalabra como
# palabras ("bacter" + "ias"): partía palabras al medio y multiplicaba las
# síntesis (~50 para una respuesta de 1200 chars) con prosodia entrecortada.
DEFAULT_MIN_WORDS: int = 12

# The FIRST chunk flushes earlier: it gates time-to-first-audio, and a shorter text
# also synthesizes faster. Later chunks use DEFAULT_MIN_WORDS for natural prosody.
DEFAULT_FIRST_MIN_WORDS: int = 6

# Clause punctuation preferred for a soft flush (pause-friendly for TTS).
_CLAUSE_ENDS: frozenset[str] = frozenset({',', ';', ':'})


def split_tts_sentences(
    text: str, clean: Optional[Callable[[str], str]] = None
) -> list[str]:
    """
    Parte `text` completo en oraciones para TTS (cierra en SENTENCE_ENDS).

    Si `clean` se pasa, se aplica a cada oración y las que quedan vacías tras
    limpiar se descartan. Útil para sanitización chunk-safe.
    """
    sentences: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in SENTENCE_ENDS:
            s = buf.strip()
            if s:
                sentences.append(s)
            buf = ""
    tail = buf.strip()
    if tail:
        sentences.append(tail)

    if clean is not None:
        cleaned = []
        for s in sentences:
            c = clean(s)
            if c and c.strip():
                cleaned.append(c.strip())
        return cleaned
    return sentences


def iter_stream_chunks(
    tokens: Iterable[str],
    min_words: int = DEFAULT_MIN_WORDS,
    clean: Optional[Callable[[str], str]] = None,
    first_min_words: Optional[int] = None,
) -> Iterator[str]:
    """
    Acumula tokens de un stream y emite fragmentos listos para TTS: al cerrar
    una oración (SENTENCE_ENDS) o cuando el buffer supera `min_words` palabras.

    Generador puro (sin asyncio/TTS), para poder testearlo y medirlo aislado.
    Nunca parte palabras: los tokens del LLM son subpalabras, así que sólo se
    corta en un espacio. '.', '!' y '?' cierran oración sólo si les sigue un
    espacio (así "3.5" o "v2.1" no se parten); '\n' cierra siempre. Un
    delimitador al final del buffer espera al próximo token para decidir.
    Si `clean` se pasa, se aplica a cada fragmento y los vacíos se omiten.
    """
    buf = ""

    def _sentence_cut(s: str) -> int:
        """Index just past the last sentence boundary in `s`, or 0."""
        cut = 0
        for i, ch in enumerate(s):
            if ch == '\n':
                cut = i + 1
            elif ch in SENTENCE_ENDS and i + 1 < len(s) and s[i + 1].isspace():
                cut = i + 1
        return cut

    # None → same threshold for every chunk; the streaming pump passes DEFAULT_FIRST_MIN_WORDS.
    first_min = min(first_min_words, min_words) if first_min_words is not None else min_words

    def _soft_cut(s: str, limit: int) -> int:
        """Word-boundary cut for a long run-on buffer: prefer clause punctuation."""
        last_space = -1
        for i in range(len(s) - 1, 0, -1):
            if s[i].isspace():
                if s[i - 1] in _CLAUSE_ENDS and len(s[:i].split()) >= limit // 2:
                    return i
                if last_space < 0:
                    last_space = i
        return max(last_space, 0)

    def _prepare(s: str) -> Optional[str]:
        s = s.strip()
        if not s:
            return None
        if clean is not None:
            s = clean(s)
            if not s or not s.strip():
                return None
            s = s.strip()
        return s

    emitted = False
    for token in tokens:
        if not token:
            continue
        buf += token
        limit = min_words if emitted else first_min
        cut = _sentence_cut(buf)
        if not cut and len(buf.split()) > limit:
            cut = _soft_cut(buf, limit)
        if cut:
            out = _prepare(buf[:cut])
            if out is not None:
                emitted = True
                yield out
            buf = buf[cut:]

    out = _prepare(buf)
    if out is not None:
        yield out


def count_sentences(text: str) -> int:
    """Nº de oraciones según la misma heurística (para métricas/umbrales)."""
    return len(split_tts_sentences(text))
