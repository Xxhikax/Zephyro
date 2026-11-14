"""
Tests de la util de fragmentación para streaming TTS (modules/voice/tts_chunking.py).

Cubre split por oración, flush por umbral de palabras, no romper palabras,
sanitización chunk-safe (callback `clean`) e inputs vacíos.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.voice.tts_chunking import (  # noqa: E402
    split_tts_sentences,
    iter_stream_chunks,
    count_sentences,
    DEFAULT_MIN_WORDS,
)


# ── split_tts_sentences ───────────────────────────────────────────────────────

def test_split_basic_sentences():
    assert split_tts_sentences("Hola mundo. ¿Cómo estás? Bien!") == [
        "Hola mundo.",
        "¿Cómo estás?",
        "Bien!",
    ]


def test_split_trailing_without_punctuation():
    # La última oración sin signo final igual se emite.
    assert split_tts_sentences("Primera. Segunda sin punto") == [
        "Primera.",
        "Segunda sin punto",
    ]


def test_split_newline_is_boundary():
    assert split_tts_sentences("Línea uno\nLínea dos.") == ["Línea uno", "Línea dos."]


def test_split_empty_and_whitespace():
    assert split_tts_sentences("") == []
    assert split_tts_sentences("   \n  ") == []


def test_split_clean_callback_drops_empties():
    # clean que borra markdown; oraciones que quedan vacías se descartan.
    # La oración del medio ("**") queda vacía tras quitar '*' → se descarta.
    clean = lambda s: s.replace("*", "").strip()
    assert split_tts_sentences("**Hola**\n**\nChau.", clean=clean) == ["Hola", "Chau."]


def test_count_sentences():
    assert count_sentences("Una. Dos. Tres.") == 3
    assert count_sentences("Sin puntuacion") == 1
    assert count_sentences("") == 0


# ── iter_stream_chunks ────────────────────────────────────────────────────────

def test_stream_emits_on_sentence_end():
    tokens = ["Hola", " mundo.", " ¿Cómo", " estás?"]
    assert list(iter_stream_chunks(tokens)) == ["Hola mundo.", "¿Cómo estás?"]


def test_stream_does_not_break_words():
    # Tokens que parten palabras a la mitad: el buffer las reensambla.
    tokens = ["Ho", "la", " mun", "do."]
    assert list(iter_stream_chunks(tokens)) == ["Hola mundo."]


def test_stream_word_flush_threshold():
    # Sin puntuación: descarga al superar min_words (fallback de run-on).
    words = ["w%d " % i for i in range(DEFAULT_MIN_WORDS + 2)]
    chunks = list(iter_stream_chunks(words, min_words=DEFAULT_MIN_WORDS))
    assert len(chunks) >= 1
    # El primer chunk tiene al menos min_words palabras.
    assert len(chunks[0].split()) >= DEFAULT_MIN_WORDS


def test_stream_skips_empty_tokens():
    tokens = ["", "Hola", "", " mundo.", ""]
    assert list(iter_stream_chunks(tokens)) == ["Hola mundo."]


def test_stream_flushes_tail_without_punctuation():
    tokens = ["Cola", " final"]
    assert list(iter_stream_chunks(tokens, min_words=99)) == ["Cola final"]


def test_stream_clean_applied_per_chunk():
    clean = lambda s: s.replace("*", "").strip()
    tokens = ["**Hola", " mundo**.", " Chau."]
    assert list(iter_stream_chunks(tokens, clean=clean)) == ["Hola mundo.", "Chau."]


def test_stream_clean_dropping_empty_chunk():
    # Un chunk que queda vacío tras limpiar no se emite (boundary '\n').
    clean = lambda s: s.replace("#", "").strip()
    tokens = ["##\n", " Real."]
    assert list(iter_stream_chunks(tokens, clean=clean)) == ["Real."]


def test_stream_empty_input():
    assert list(iter_stream_chunks([])) == []
    # Tokens sólo de whitespace no producen fragmentos.
    assert list(iter_stream_chunks(["", "  ", "\t"])) == []


# ── Latency audit 2026-10: LLM tokens are SUBWORDS ────────────────────────────

def test_stream_never_splits_subword_tokens_on_soft_flush():
    """Real DeepSeek token shapes: the soft flush used to count tokens as words and cut
    "bacter|ias" / "lumin|osa" into separate TTS chunks."""
    toks = ['La', ' fotos', 'íntesis', ' es', ' el', ' proceso', ' mediante', ' el', ' cual',
            ' las', ' plantas', ',', ' alg', 'as', ' y', ' algunas', ' bacter', 'ias',
            ' transform', 'an', ' la', ' energía', ' lumin', 'osa', ' en', ' energía', '.']
    chunks = list(iter_stream_chunks(iter(toks)))
    joined = " ".join(chunks)
    for word in ("fotosíntesis", "algas", "bacterias", "transforman", "luminosa"):
        assert word in joined, f"{word!r} was split across chunks: {chunks}"


def test_stream_soft_flush_prefers_clause_punctuation():
    toks = ("uno dos tres cuatro cinco seis siete, ocho nueve diez once doce trece "
            "catorce").split(" ")
    toks = [t if i == 0 else " " + t for i, t in enumerate(toks)]
    chunks = list(iter_stream_chunks(iter(toks)))
    assert chunks[0].endswith("siete,"), chunks


def test_stream_decimal_point_is_not_a_sentence_end():
    toks = ['Cuesta', ' 3', '.', '5', ' dólares', '.', ' Listo', '.']
    assert list(iter_stream_chunks(iter(toks))) == ["Cuesta 3.5 dólares.", "Listo."]


def test_stream_period_inside_token_cuts_at_the_boundary():
    # A token carrying the end of one sentence and the start of the next.
    toks = ['Primera', ' oración', '. Seg', 'unda', ' oración', '.']
    assert list(iter_stream_chunks(iter(toks))) == ["Primera oración.", "Segunda oración."]


def test_stream_first_chunk_flushes_earlier_than_the_rest():
    words = [("" if i == 0 else " ") + f"w{i}" for i in range(40)]
    chunks = list(iter_stream_chunks(iter(words), first_min_words=6))
    assert len(chunks[0].split()) <= 8, chunks[0]
    assert all(len(c.split()) >= 10 for c in chunks[1:-1]), chunks
