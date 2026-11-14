"""Fast path de test de micrófono en core/fast_responses.py.

Contexto: "1, 2, 3, probando." tardaba muchísimo en modo call. No había fast path que
lo atrapara — `FAST_CACHE` es dict exacto (31 saludos/despedidas/acks) y el `_RE_MATH`
del router exige que el input sea SOLO dígitos, así que la palabra "probando" tiraba el
turno al pipeline cloud completo (CAPA 2 Haiku + 2 embeddings + tool loop).

Lo delicado del fix no es matchear los tests de micrófono, es NO matchear una frase real
que contenga "probando". De ahí el doble requisito: un trigger fuerte Y que todos los
demás tokens sean relleno o números. Estos tests cubren las dos direcciones; los
negativos son los que importan.
"""
import pytest

from core.fast_responses import lookup, _is_mic_test, _mic_test_response


MIC_TESTS = [
    "probando",
    "Probando",
    "probando probando",
    "1, 2, 3, probando.",
    "1 2 3 probando",
    "uno dos tres probando",
    "probando 1 2 3",
    "hola probando",
    "probando zephyro",
    "probando, zephyro",
    "me escuchas?",
    "¿me escuchás?",
    "me escuchas bien",
    "se escucha bien",
    "me oyes",
    "test",
    "prueba de microfono",
    "prueba de micrófono",
    "probando el microfono",
]

NOT_MIC_TESTS = [
    # Contienen un trigger pero son preguntas reales → pipeline completo.
    "estoy probando el codigo de Docker",
    "probando una nueva arquitectura hexagonal",
    "prueba a conectarte con la API de Stripe y traeme el log",
    "escribi un test para el router",
    "corre las pruebas del backend",
    "me escuchas cuando hablo de arquitectura limpia",
    # Sin ningún trigger.
    "como va el proyecto",
    "explicame el patron repository",
    "1 2 3",
    "uno dos tres",
    "si",
    "no",
]


@pytest.mark.parametrize("text", MIC_TESTS)
def test_mic_test_is_detected(text):
    assert lookup(text) == _mic_test_response()


@pytest.mark.parametrize("text", NOT_MIC_TESTS)
def test_non_mic_test_is_not_answered_as_one(text):
    result = lookup(text)
    assert result != _mic_test_response()


def test_unknown_token_rejects_even_with_trigger():
    """Un solo token desconocido alcanza para descartar: es una frase real."""
    assert _is_mic_test("probando") is True
    assert _is_mic_test("probando docker") is False


def test_word_cap_rejects_long_utterances():
    assert _is_mic_test("hola probando uno dos tres zephyro") is True   # 6 tokens
    assert _is_mic_test("hola probando uno dos tres cuatro cinco") is False  # 7


def test_digits_alone_are_not_a_mic_test():
    """Sin trigger no hay match: '1 2 3' pelado ya lo atrapa _RE_MATH en el router."""
    assert _is_mic_test("1 2 3") is False


def test_existing_fast_cache_entries_still_win():
    """El mic test se evalúa DESPUÉS de FAST_CACHE y de los dynamic handlers."""
    assert lookup("hola") != _mic_test_response()
    assert lookup("gracias") != _mic_test_response()
    assert lookup("que hora es") != _mic_test_response()
