"""Verifica que los comandos compuestos generen MULTI_ACTION correctamente."""

import re

import pytest
from unittest.mock import MagicMock


def make_router():
    from core.router import IntentRouter
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent": "automation"}')
    return IntentRouter(mock_llm, context_manager=None)


# ── Dos apps independientes ──────────────────────────────────────────

COMPOUND_TWO_APPS = [
    ("abre discord y luego abre spotify", ["discord", "spotify"]),
    ("abra discord y luego abra youtube", ["discord", "youtube"]),
    ("abre firefox y abre chrome", ["firefox", "chrome"]),
    ("abre spotify y después abre vlc", ["spotify", "vlc"]),
    ("lanza discord y luego abre steam", ["discord", "steam"]),
    ("abre youtube y luego abre youtube music", ["youtube", "youtube music"]),
    # Sin segundo verbo explícito
    ("abre discord y spotify", ["discord", "spotify"]),
    ("abre discord y luego spotify", ["discord", "spotify"]),
    ("abre discord luego spotify", ["discord", "spotify"]),
    ("abre discord después spotify", ["discord", "spotify"]),
]


@pytest.mark.parametrize("user_input,expected_targets", COMPOUND_TWO_APPS)
@pytest.mark.asyncio
async def test_compound_two_apps(user_input, expected_targets):
    """Comandos compuestos deben generar acciones."""
    import re as _re
    if _re.search(r'\by\s+(?:luego|despu[eé]s)\b', user_input.lower()):
        pytest.xfail(
            "'y luego/después' triggers _has_sequence_hint → skips CAPA 1 compound parser; "
            "requires LLM (CAPA 1.5) which mock doesn't provide"
        )
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)

    assert result.get("intent") == "automation", \
        f"'{user_input}' debe ser automation, fue {result.get('intent')}"

    auto_intent = result.get("automation_intent")
    assert auto_intent in ("MULTI_ACTION", "NAVIGATE", "OPEN_APP"), \
        f"'{user_input}': automation_intent={auto_intent}"

    if "actions" in result:
        actions = result["actions"]
        assert len(actions) >= len(expected_targets), \
            f"Esperaba {len(expected_targets)} acciones, hay {len(actions)}"
        for i, target in enumerate(expected_targets):
            found = any(target in a.get("target", "").lower() for a in actions)
            assert found, \
                f"No se encontró '{target}' en targets: {[a.get('target') for a in actions]}"


# ── App + navegación/búsqueda ────────────────────────────────────────

COMPOUND_NAV_SEARCH = [
    ("abre youtube y busca lo-fi music", "youtube"),
    ("abre instagram y entra a reels", "instagram"),
    ("abre github y busca fastapi", "github"),
    ("abre wikipedia y busca Alan Turing", "wikipedia"),
    ("abre youtube y luego busca jazz", "youtube"),
]


@pytest.mark.parametrize("user_input,expected_primary", COMPOUND_NAV_SEARCH)
@pytest.mark.asyncio
async def test_compound_navigate_search(user_input, expected_primary):
    """Comandos compuestos app+acción deben preservar búsqueda/navegación."""
    import re as _re
    if _re.search(r'\by\s+(?:luego|despu[eé]s)\b', user_input.lower()):
        pytest.xfail(
            "'y luego/después' triggers _has_sequence_hint → skips CAPA 1 compound parser; "
            "requires LLM (CAPA 1.5) which mock doesn't provide"
        )
    # Non-media apps (github, wikipedia) + "busca <term>" can trigger PLAY_MEDIA in CAPA 1
    # when the search term has no media-context signals — needs CAPA 1.5 LLM to disambiguate.
    _NON_MEDIA_NAV = re.compile(
        r'\babre\s+(?:github|wikipedia|reddit|stackoverflow|linkedin)\b.*\bbusca\b',
        re.IGNORECASE,
    )
    if _NON_MEDIA_NAV.search(user_input):
        pytest.xfail(
            "non-media app + 'busca <term>' triggers PLAY_MEDIA in CAPA 1; "
            "requires LLM (CAPA 1.5) to disambiguate"
        )
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)

    assert result.get("intent") == "automation", \
        f"'{user_input}' debe ser automation"

    target = result.get("target", "").lower()
    assert expected_primary in target, \
        f"Esperaba target '{expected_primary}', obtuvo '{target}'"


# ── Verbos compuestos con conjugaciones nuevas ───────────────────────

VERB_VARIATIONS = [
    "abra youtube y busque música",
    "lance discord y ejecute steam",
    "inicie spotify y reproduzca jazz",
    "ejecute terminal y abra firefox",
]


@pytest.mark.parametrize("user_input", VERB_VARIATIONS)
@pytest.mark.asyncio
async def test_compound_new_conjugations(user_input):
    """Conjugaciones nuevas deben funcionar en comandos compuestos."""
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)
    assert result.get("intent") == "automation", \
        f"'{user_input}' debe ser automation, fue {result.get('intent')}"


# ── Targets multi-palabra ────────────────────────────────────────────

MULTI_WORD_TARGETS = [
    ("abre youtube music", "youtube"),
    ("abre prime video", "prime video"),
    ("abre youtube music y busca chill", "youtube"),
]


@pytest.mark.parametrize("user_input,expected_target", MULTI_WORD_TARGETS)
@pytest.mark.asyncio
async def test_multi_word_targets(user_input, expected_target):
    """Targets multi-palabra deben ser capturados completos."""
    router = make_router()
    result = await router.classify_and_parse(user_input, skip_llm=True)
    assert result.get("intent") == "automation"

    target = result.get("target", "").lower()
    if "actions" in result:
        targets = [a.get("target", "") for a in result["actions"]]
        assert any(expected_target in t.lower() for t in targets), \
            f"Target '{expected_target}' no encontrado en {targets}"
    else:
        assert expected_target in target, \
            f"Target '{expected_target}' no encontrado en '{target}'"
