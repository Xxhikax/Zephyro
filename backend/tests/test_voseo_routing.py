"""
El router entiende voseo rioplatense ("poné firefox", "abrí discord", "buscá X").

Antes, AUTO_VERBS solo tenía formas tuteo/imperativo estándar ("pon", "abre"),
y "\\bagenda\\b" no matchea "agendá". Además "abrí" colisionaba con el guard
conversacional (pasado "yo abrí") y quedaba bloqueado. Acá verificamos que las
formas voseo se reconocen como automation y que el guard ya no bloquea "abrí X".
"""
import pytest

from core.app_config import AUTO_VERBS


def test_auto_verbs_includes_voseo():
    for v in ("abrí", "poné", "reproducí", "buscá", "cerrá", "ejecutá",
              "lanzá", "iniciá", "activá"):
        assert v in AUTO_VERBS, f"falta forma voseo '{v}' en AUTO_VERBS"


def _router():
    from core.router import IntentRouter
    # classify_and_parse con skip_llm=True corre solo heurísticas, sin LLM.
    return IntentRouter(None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "poné firefox",
    "abrí discord",
    "reproducí lofi en youtube",
])
async def test_voseo_commands_classified_as_automation(text):
    router = _router()
    analysis = await router.classify_and_parse(text, skip_llm=True)
    # poné/abrí/reproducí + app/sitio → automation (no charla casual).
    assert analysis.get("intent") in ("automation", "search", "web_search"), \
        f"{text!r} → {analysis.get('intent')} ({analysis.get('_source')})"


@pytest.mark.asyncio
async def test_voseo_search_recognized():
    # "buscá X en internet" → búsqueda web (information en Zephyro). Lo que
    # importa: "buscá" se reconoce como verbo, no se trata como charla casual.
    router = _router()
    analysis = await router.classify_and_parse("buscá gatos en internet", skip_llm=True)
    assert analysis.get("intent") in ("information", "search", "web_search", "automation")


@pytest.mark.asyncio
async def test_abri_imperative_not_blocked_as_conversational():
    # "abrí discord" (imperativo voseo) NO debe tratarse como info/chat.
    router = _router()
    analysis = await router.classify_and_parse("abrí discord", skip_llm=True)
    assert analysis.get("intent") == "automation", \
        f"abrí discord quedó como {analysis.get('intent')}"


@pytest.mark.asyncio
async def test_abriste_still_conversational():
    # "ya abriste discord" (pasado 2da persona) SIGUE siendo conversacional.
    router = _router()
    analysis = await router.classify_and_parse("ya abriste discord", skip_llm=True)
    assert analysis.get("intent") != "automation", \
        "ya abriste discord no debería ser automation (es una referencia pasada)"


def test_cloud_automation_regex_includes_voseo():
    # En cloud mode el orchestrator filtra con _RE_CLOUD_AUTOMATION qué va al
    # router. Sin voseo, "poné lofi" se iba al chat y el LLM alucinaba la acción.
    from core.orchestrator import _RE_CLOUD_AUTOMATION
    for cmd in ("poné lofi en youtube music", "abrí discord", "reproducí lofi",
                "buscá gatos", "cerrá spotify"):
        assert _RE_CLOUD_AUTOMATION.search(cmd.lower()), \
            f"_RE_CLOUD_AUTOMATION no matchea voseo: {cmd!r}"


@pytest.mark.asyncio
async def test_voseo_media_routes_in_cloud_mode():
    # "poné lofi en youtube music" en cloud mode (skip_llm) → automation, no chat.
    router = _router()
    analysis = await router.classify_and_parse(
        "poné lofi en youtube music", skip_llm=True)
    assert analysis.get("intent") == "automation", \
        f"debería ser automation en cloud mode, fue {analysis.get('intent')}"
