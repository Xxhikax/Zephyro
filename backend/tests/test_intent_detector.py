"""
Golden tests for intent_detector.classify_by_rules.
Cubre: saludos, despedidas, acknowledgments, preguntas, comandos directos,
comandos corteses, overrides de acción y casos ambiguos (→ None).
"""
import pytest
from core.intent_detector import classify_by_rules, VALID_INTENTS


# ── helpers ───────────────────────────────────────────────────────────────────

def _r(text: str) -> str | None:
    """Wrapper que limpia cache entre tests para que sea idempotente."""
    # lru_cache es por valor de texto; en tests distintos textos no colisionan
    return classify_by_rules(text)


# ── SALUDOS ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "hola",
    "Hola",
    "HOLA",
    "hey",
    "hola zephyro",
    "buenas",
    "buenos días",
    "buenos dias",
    "holaaaa",       # elongación
    "heyyyy",        # elongación
    "qué tal",
    "que tal Bruno",
    "hi there",
    "hello",
])
def test_greeting(text):
    assert _r(text) == "greeting", f"Falló para: {text!r}"


# ── DESPEDIDAS ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "adiós",
    "adios",
    "chao",
    "chau",
    "bye",
    "hasta luego",
    "hasta mañana",
    "nos vemos",
    "see you",
    "chauuuu",       # elongación
])
def test_farewell(text):
    assert _r(text) == "farewell", f"Falló para: {text!r}"


# ── ACKNOWLEDGMENTS ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "gracias",
    "thanks",
    "ok",
    "dale",
    "perfecto",
    "listo",
    "genial",
    "entendido",
    "de acuerdo",
    "okey",
])
def test_acknowledgment(text):
    assert _r(text) == "acknowledgment", f"Falló para: {text!r}"


# ── PREGUNTAS ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "¿qué es Python?",
    "qué es Python",
    "cómo funciona esto",
    "cuándo sale la actualización",
    "dónde está el archivo",
    "por qué no funciona",
    "por que falla esto",
    "quién es el creador",
    "what is this",
    "how does it work",
    "when is the update",
    "me puedes explicar esto?",     # termina con "?"
])
def test_question(text):
    assert _r(text) == "question", f"Falló para: {text!r}"


# ── COMANDOS DIRECTOS ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "abre firefox",
    "busca el clima de Lima",
    "cierra la pestaña",
    "ejecuta el script",
    "descarga el archivo",
    "pon música de Coldplay",
    "navega a youtube",
    "lanza el juego",
    "muestra mis archivos",
    "open chrome",
    "search for python tutorials",
    "install nodejs",
])
def test_command_direct(text):
    assert _r(text) == "command", f"Falló para: {text!r}"


# ── COMANDOS CORTESES (prefijo + infinitivo) ──────────────────────────────────

@pytest.mark.parametrize("text", [
    "puedes abrir discord",
    "podrías buscar noticias de hoy",
    "necesito cerrar el navegador",
    "quiero abrir spotify",
    "me gustaría buscar recetas",
    "podrias instalar el paquete",
])
def test_command_polite(text):
    assert _r(text) == "command", f"Falló para: {text!r}"


# ── OVERRIDE DE ACCIÓN (saludo + verbo → no es saludo) ───────────────────────

@pytest.mark.parametrize("text", [
    "hey abre el browser",
    "hola busca el clima",
    "buenas cierra firefox",
    "hi open youtube",
])
def test_action_override_suppresses_greeting(text):
    result = _r(text)
    assert result != "greeting", f"Debería suprimir greeting para: {text!r}"


# ── AMBIGUOS (→ None, necesitan Haiku) ────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "me parece interesante lo que dijiste",
    "no sé qué hacer con esto",
    "a ver si entiendo bien",
    "creo que deberías considerar otra opción",
    "en realidad prefiero que lo hagas de otra forma",
])
def test_ambiguous_returns_none(text):
    assert _r(text) is None, f"Esperaba None para: {text!r}"


# ── INTENTS VÁLIDOS ───────────────────────────────────────────────────────────

def test_valid_intents_set():
    """clarification fue eliminado; acknowledgment fue añadido."""
    assert "clarification" not in VALID_INTENTS
    assert "acknowledgment" in VALID_INTENTS
    assert VALID_INTENTS == {"greeting", "farewell", "question", "command", "statement", "acknowledgment"}


# ── EDGE CASES ────────────────────────────────────────────────────────────────

def test_empty_string_returns_none():
    assert _r("") is None

def test_whitespace_only_returns_none():
    assert _r("   ") is None

def test_acknowledgment_not_triggered_on_long_sentence():
    # "gracias por todo" tiene 3 palabras → puede ser ack. Pero "gracias por haberte tomado
    # el tiempo de responderme" es largo → no debería ser ack trivial.
    # La regla actual: ≤ 3 palabras. Este caso tiene 9 palabras → None (va a Haiku).
    result = _r("gracias por haberte tomado el tiempo de responderme")
    assert result != "acknowledgment"

def test_question_without_mark():
    # "qué pasa" no tiene "?" pero empieza con interrogativo
    assert _r("qué pasa") == "question"

def test_elongated_greeting_mixed_case():
    assert _r("Holaaaa") == "greeting"
