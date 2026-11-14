"""Verifica que TODAS las conjugaciones de automation sean reconocidas por el router."""

import pytest
from unittest.mock import MagicMock


def make_router():
    from core.router import IntentRouter
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent": "automation"}')
    return IntentRouter(mock_llm, context_manager=None)


# ── Verbos que deben activar automation ───────────────────────────────

VERB_TEST_CASES = [
    # Abrir — indicativo
    ("abre discord", "open"),
    ("abres discord", "open"),
    ("abren discord", "open"),
    ("abrir discord", "open"),
    # Abrir — subjuntivo (NUEVOS)
    ("abra discord", "open"),
    ("abras discord", "open"),
    ("abran discord", "open"),
    ("abrirme discord", "open"),
    ("ábreme discord", "open"),
    ("ábrelo en youtube", "open"),
    # Lanzar — indicativo
    ("lanza firefox", "open"),
    # Lanzar — subjuntivo (NUEVOS)
    ("lance firefox", "open"),
    ("lances firefox", "open"),
    ("lancen firefox", "open"),
    # Ejecutar — subjuntivo (NUEVOS)
    ("ejecute terminal", "open"),
    ("ejecutes terminal", "open"),
    ("ejecuten terminal", "open"),
    # Iniciar — subjuntivo (NUEVOS)
    ("inicie spotify", "open"),
    ("inicies spotify", "open"),
    ("inicien spotify", "open"),
    # Activar
    ("active discord", "open"),
    # Reproducir
    ("reproduce música", "open"),
    # Buscar — subjuntivo (NUEVOS)
    ("busque gatos", "search"),
    ("busques gatos", "search"),
    ("busquen gatos", "search"),
    # Open (inglés)
    ("open firefox", "open"),
    ("run terminal", "open"),
    ("launch spotify", "open"),
]


@pytest.mark.parametrize("user_input,expected_action", VERB_TEST_CASES)
@pytest.mark.asyncio
async def test_verb_triggers_automation(user_input, expected_action):
    """Cada conjugación debe ser reconocida como automation."""
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)
    assert result.get("intent") == "automation", \
        f"'{user_input}' debería ser automation, fue {result.get('intent')}"


# ── Negaciones: NO deben activar automation ──────────────────────────

NEGATION_GUARD_CASES = [
    "no abras nada",
    "no abra nada",
    "no busques eso",
    "no busque eso",
    "no lances firefox",
    "no lance firefox",
    "no ejecutes steam",
    "no ejecute steam",
    "no me abras la terminal",
    "no me abra la terminal",
    "no navegues en youtube",
]


@pytest.mark.parametrize("user_input", NEGATION_GUARD_CASES)
@pytest.mark.asyncio
async def test_negation_guard_blocks_automation(user_input):
    """Las negaciones con cualquier conjugación NO deben ser automation."""
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)
    assert result.get("intent") != "automation", \
        f"'{user_input}' no debería activar automation (es negación), fue {result.get('intent')}"


# ── Subordinadas ─────────────────────────────────────────────────────

SUBORDINATE_GUARD_CASES = [
    "para que puedas abrir discord",
    "para que pueda abrir firefox",
    "capaz de abrir apps",
    "pienso darte una skill para abrir apps",
    "necesito que seas capaz de abrir chrome",
    "la idea es que ejecutes comandos",
    "para abrir terminal necesito permisos",
    "poder ejecutar comandos remotos",
]


@pytest.mark.parametrize("user_input", SUBORDINATE_GUARD_CASES)
@pytest.mark.asyncio
async def test_subordinate_clause_not_automation(user_input):
    """Cláusulas subordinadas NO deben ser automation."""
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)
    assert result.get("intent") != "automation", \
        f"'{user_input}' es subordinada, fue {result.get('intent')}"


# ── Conversacionales ─────────────────────────────────────────────────

CONVERSATIONAL_CASES = [
    "por qué no pudiste abrir discord",
    "cómo es que no abriste firefox",
    "ya abriste discord en firefox",
    "por qué falló la automatización",
    "no te pedí que abras nada",
    "en ningún momento te pedí abrir eso",
]


@pytest.mark.parametrize("user_input", CONVERSATIONAL_CASES)
@pytest.mark.asyncio
async def test_conversational_not_automation(user_input):
    """Frases conversacionales sobre automation NO deben ejecutar automation."""
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)
    assert result.get("intent") != "automation", \
        f"'{user_input}' es conversacional, fue {result.get('intent')}"


# ── Chats legítimos ──────────────────────────────────────────────────

CHAT_NOT_AUTOMATION = [
    "hola cómo estás",
    "qué significa machine learning",
    "cuál es la capital de Francia",
    "explícame la teoría de la relatividad",
    "cómo se hace una tortilla de patatas",
    "no me abras nada todavía",
    "por qué no funciona mi código",
    "dame un consejo para dormir mejor",
]


@pytest.mark.parametrize("user_input", CHAT_NOT_AUTOMATION)
@pytest.mark.asyncio
async def test_chat_not_automation(user_input):
    """Frases de chat no deben clasificarse como automation."""
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)
    assert result.get("intent") != "automation", \
        f"'{user_input}' NO debe ser automation, fue {result.get('intent')}"
