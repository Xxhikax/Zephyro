"""Tests unitarios para los filtros TTS de response_sanitizer.prepare_for_tts()"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.response_sanitizer import prepare_for_tts, _strip_english_sentences


# ── Filtro de fuentes ─────────────────────────────────────────────────────────

def test_strips_fuentes_block():
    text = "Hubo un temblor en México.\n\nFuentes:\n• CNN\n• Univision"
    result = prepare_for_tts(text)
    assert "CNN" not in result
    assert "Univision" not in result
    assert "Hubo un temblor" in result


def test_strips_fuentes_inline():
    text = "Los precios subieron un 3%.\nFuentes: Reuters, Bloomberg"
    result = prepare_for_tts(text)
    assert "Reuters" not in result
    assert "Bloomberg" not in result
    assert "precios subieron" in result


def test_strips_sources_english():
    text = "El partido terminó 2-1.\nSources: ESPN, Sky Sports"
    result = prepare_for_tts(text)
    assert "ESPN" not in result
    assert "partido terminó" in result


def test_fuentes_case_insensitive():
    text = "Noticias del día.\nFUENTES: CNN, BBC"
    result = prepare_for_tts(text)
    assert "CNN" not in result
    assert "BBC" not in result


# ── Filtro de atribuciones ────────────────────────────────────────────────────

def test_strips_segun_attribution():
    text = "Según Reuters, hubo ataques en la frontera norte del país."
    result = prepare_for_tts(text)
    assert "Reuters" not in result
    assert "hubo ataques" in result


def test_strips_de_acuerdo_attribution():
    text = "De acuerdo con El País, el gobierno anunció medidas económicas."
    result = prepare_for_tts(text)
    assert "El País" not in result
    assert "gobierno anunció" in result


# ── Filtro markdown ───────────────────────────────────────────────────────────

def test_strips_markdown_headers():
    text = "# Últimas noticias\nHubo varios eventos hoy."
    result = prepare_for_tts(text)
    assert "# " not in result
    assert "Últimas noticias" in result
    assert "varios eventos" in result


def test_strips_h2_header():
    text = "## Resumen del día\nEl mercado cerró al alza."
    result = prepare_for_tts(text)
    assert "## " not in result
    assert "Resumen del día" in result


def test_strips_bullets():
    text = "Puntos clave:\n• Primer punto\n- Segundo punto\n* Tercer punto"
    result = prepare_for_tts(text)
    assert "• " not in result
    assert "- " not in result
    assert "* " not in result
    assert "Primer punto" in result
    assert "Segundo punto" in result


# ── Filtro de URLs ────────────────────────────────────────────────────────────

def test_strips_urls():
    text = "Puedes leerlo en https://cnn.com/noticias/articulo-123 para más info."
    result = prepare_for_tts(text)
    assert "https://" not in result
    assert "más info" in result


# ── Filtro de inglés ─────────────────────────────────────────────────────────

def test_strips_english_only_line():
    """Línea puramente en inglés ya no se elimina por completo — se devuelve
    el texto original para evitar que prepare_for_tts entregue texto vacío (P3)."""
    text = "gold in the ceiling the song"
    result = _strip_english_sentences(text)
    assert result.strip() == text


def test_strips_english_sentence_in_mixed_text():
    text = "Te voy a poner una canción.\ngold in the ceiling by the black keys\nEspero que te guste."
    result = prepare_for_tts(text)
    assert "gold in the ceiling" not in result
    assert "Te voy a poner" in result
    assert "Espero que te guste" in result


def test_keeps_spanish_with_tech_terms():
    """Palabras técnicas en inglés dentro de oraciones españolas no deben eliminarse."""
    text = "abre YouTube en Firefox"
    result = prepare_for_tts(text)
    assert "YouTube" in result
    assert "Firefox" in result


def test_keeps_spanish_sentence_mixed():
    text = "la API de Google está disponible para desarrolladores"
    result = prepare_for_tts(text)
    # La oración es mayoritariamente española — no debe eliminarse
    assert "API" in result or "disponible" in result


def test_keeps_short_lines_unchanged():
    """Líneas con menos de 3 palabras no se filtran (no hay suficiente señal)."""
    text = "OK\nFine\nYes"
    result = _strip_english_sentences(text)
    # Líneas cortas pasan sin filtrar
    assert "OK" in result


# ── Idempotencia ──────────────────────────────────────────────────────────────

def test_idempotent():
    text = "# Noticias\n- Hubo lluvias.\nFuentes: CNN\nhttps://cnn.com"
    once = prepare_for_tts(text)
    twice = prepare_for_tts(once)
    assert once == twice


def test_empty_string():
    assert prepare_for_tts("") == ""


def test_none_safe():
    # La función recibe str, pero validamos que no explota con string vacío
    result = prepare_for_tts("   ")
    assert result == ""


# ── Texto normal español sin ruido ────────────────────────────────────────────

def test_normal_spanish_preserved():
    text = "El clima en Ciudad de México será soleado con temperaturas de 24 grados."
    result = prepare_for_tts(text)
    assert result.strip() == text.strip()


def test_news_response_typical():
    text = (
        "Según el Servicio Meteorológico Nacional, habrá lluvias en el norte del país.\n"
        "Las temperaturas oscilarán entre 18 y 25 grados.\n\n"
        "Fuentes:\n• SMN\n• Weather.com"
    )
    result = prepare_for_tts(text)
    assert "SMN" not in result
    assert "Weather.com" not in result
    assert "habrá lluvias" in result
    assert "temperaturas" in result


# ── Unicode normalization (NFD → NFC) ──────────────────────────────────────

def test_nfc_normalization_preserves_precomposed_accents():
    """Texto con tildes NFC (normal) se preserva igual."""
    text = "revisión, canción, información"
    result = prepare_for_tts(text)
    assert "revisión" in result
    assert "canción" in result
    assert "información" in result


def test_nfc_normalization_fixes_decomposed_accents():
    """Texto con tildes NFD (decomposed) se normaliza a NFC."""
    import unicodedata
    # 'á' en NFD = 'a' + combining acute accent (U+0301)
    # 'ñ' en NFD = 'n' + combining tilde (U+0303)
    nfd = unicodedata.normalize('NFD', "revisión, canción, información")
    result = prepare_for_tts(nfd)
    assert "revisión" in result
    assert "canción" in result
    assert "información" in result
    # Los combining marks deben haberse fusionado (ya no están separados)
    assert "a\u0301" not in result  # 'á' unicode no debe contener el combining mark suelto
    assert "o\u0301" not in result  # 'ó' unicode no debe contener el combining mark suelto


def test_nfc_normalization_does_not_alter_ascii():
    """Texto ASCII puro no se modifica."""
    text = "hola mundo 123"
    result = prepare_for_tts(text)
    assert "hola mundo 123" in result


def test_nfc_vs_nfd_identity():
    """Normalizar NFD produce el mismo resultado que NFC original."""
    import unicodedata
    nfc = "revisión estándar con tildes: á é í ó ú ñ ü"
    nfd = unicodedata.normalize('NFD', nfc)
    # Ambos deben producir el mismo resultado tras prepare_for_tts
    result_nfc = prepare_for_tts(nfc)
    result_nfd = prepare_for_tts(nfd)
    assert result_nfc == result_nfd
