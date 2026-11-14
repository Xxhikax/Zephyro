#!/usr/bin/env python3
"""
Unit tests for router classification: action vs conversation confusion.

Verifies that meta-questions about Zephyro's search behavior, explicit
negations of actions, and possessive capability references are NOT
classified as automation — while legitimate search/automation commands
continue to work correctly.

Bug context (2026-03-28 Telegram):
  Zephyro opened DuckDuckGo 4 times for conversational questions like
  "como filtras las fuentes dudosas" because the router matched "busca"
  in auto_keywords without a conversational guard for meta-questions.

Usage:
  cd backend && python -m pytest tests/test_router_search_confusion.py -v
"""
import sys
import os
import asyncio
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.router import IntentRouter


def _make_router() -> IntentRouter:
    """Create a router with a mocked LLM (CAPA 2 won't be reached for CAPA 1 matches)."""
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent": "chat"}')
    return IntentRouter(mock_llm, context_manager=None)


def _classify(user_input: str) -> dict:
    """Synchronously classify a user input via the router."""
    router = _make_router()
    return asyncio.run(router.classify_and_parse(user_input))


# ═══════════════════════════════════════════════════════════════════════
# 1. Meta-questions about Zephyro's search/filter behavior → NOT automation
# ═══════════════════════════════════════════════════════════════════════

class TestMetaQuestionsNotAutomation(unittest.TestCase):
    """
    Questions about HOW Zephyro searches or filters should never trigger
    automation (which would open DuckDuckGo in the browser).
    """

    SHOULD_NOT_BE_AUTOMATION = [
        "como buscas la informacion",
        "como filtras las fuentes dudosas",
        "de donde sacas tus fuentes",
        "como es que buscas las noticias",
        "tus fuentes de informacion son confiables",
        "como analizas la informacion que encuentras",
        "de donde obtienes los datos",
        "como seleccionas las fuentes",
        "como verificas que las fuentes sean confiables",
        "cuales son tus fuentes",
        "como es que filtras las fuentes de informacion",
        "tus resultados son precisos",
        "tus metodos de busqueda son efectivos",
        "sus fuentes son fiables",
    ]

    def test_meta_questions_not_automation(self):
        """All meta-questions about Zephyro's behavior must NOT be automation."""
        failures = []
        for query in self.SHOULD_NOT_BE_AUTOMATION:
            result = _classify(query)
            if result.get("intent") == "automation":
                failures.append(f"  '{query}' → automation (should NOT be)")
        if failures:
            self.fail(
                f"{len(failures)} meta-question(s) misclassified as automation:\n"
                + "\n".join(failures)
            )

    def test_como_buscas(self):
        result = _classify("como buscas la informacion")
        self.assertNotEqual(result["intent"], "automation")

    def test_como_filtras(self):
        result = _classify("como filtras las fuentes dudosas")
        self.assertNotEqual(result["intent"], "automation")

    def test_de_donde_sacas(self):
        result = _classify("de donde sacas tus fuentes")
        self.assertNotEqual(result["intent"], "automation")

    def test_tus_fuentes(self):
        result = _classify("tus fuentes de informacion son confiables")
        self.assertNotEqual(result["intent"], "automation")

    def test_como_analizas(self):
        result = _classify("como analizas la informacion que encuentras")
        self.assertNotEqual(result["intent"], "automation")

    def test_como_obtienes(self):
        result = _classify("de donde obtienes los datos")
        self.assertNotEqual(result["intent"], "automation")

    def test_tus_resultados(self):
        result = _classify("tus resultados son precisos")
        self.assertNotEqual(result["intent"], "automation")

    def test_sus_fuentes(self):
        result = _classify("sus fuentes son fiables")
        self.assertNotEqual(result["intent"], "automation")


# ═══════════════════════════════════════════════════════════════════════
# 2. Explicit negation of actions → NOT automation
# ═══════════════════════════════════════════════════════════════════════

class TestExplicitNegationNotAutomation(unittest.TestCase):
    """
    When user explicitly says "no busques" or "no abras", the router
    must NOT trigger automation.
    """

    NEGATION_NOT_AUTOMATION = [
        "respondeme la pregunta no abras ningun navegador ni busques nada",
        "no busques nada solo responde",
        "no abras el navegador",
        "no te he pedido ninguna accion solo que respondas la pregunta",
        "no busques en internet solo dime lo que sabes",
        "no abras duckduckgo",
        "no lances ningun programa",
    ]

    def test_negation_not_automation(self):
        """All explicit negations must NOT trigger automation."""
        failures = []
        for query in self.NEGATION_NOT_AUTOMATION:
            result = _classify(query)
            if result.get("intent") == "automation":
                failures.append(f"  '{query}' → automation (should NOT be)")
        if failures:
            self.fail(
                f"{len(failures)} negation(s) misclassified as automation:\n"
                + "\n".join(failures)
            )

    def test_no_busques_nada(self):
        result = _classify("no busques nada solo responde")
        self.assertNotEqual(result["intent"], "automation")

    def test_no_abras_navegador(self):
        result = _classify("no abras el navegador")
        self.assertNotEqual(result["intent"], "automation")

    def test_no_abras_duckduckgo(self):
        result = _classify("no abras duckduckgo")
        self.assertNotEqual(result["intent"], "automation")

    def test_compound_negation(self):
        result = _classify("respondeme la pregunta no abras ningun navegador ni busques nada")
        self.assertNotEqual(result["intent"], "automation")


# ═══════════════════════════════════════════════════════════════════════
# 3. Legitimate automation/search commands → MUST remain automation
# ═══════════════════════════════════════════════════════════════════════

class TestLegitimateSearchRemains(unittest.TestCase):
    """
    Real automation commands must NOT be blocked by the new guards.
    """

    def test_busca_tutorial_youtube(self):
        result = _classify("busca tutorial de python en youtube")
        self.assertEqual(result["intent"], "automation")

    def test_abre_youtube_busca(self):
        result = _classify("abre youtube y busca never gonna give you up")
        self.assertEqual(result["intent"], "automation")

    def test_busca_como_hacer(self):
        """'busca como hacer X' routes to information (Tavily) or automation.
        The router correctly sends this to information with needs_web_search."""
        result = _classify("busca como hacer un pastel")
        # Both automation (browser search) and information (Tavily) are acceptable
        self.assertIn(result["intent"], ("automation", "information"))

    def test_abre_spotify_busca(self):
        result = _classify("abre spotify y busca coldplay")
        self.assertEqual(result["intent"], "automation")

    def test_busca_clima(self):
        """'busca el clima en google' — 'en google' matches _info_search_indicators → information."""
        result = _classify("busca el clima en google")
        # "en google" is an info search indicator, so this routes to information (correct)
        self.assertIn(result["intent"], ("automation", "information"))

    def test_encuentra_restaurantes(self):
        """'encuentra restaurantes cerca' routes to information (Tavily) or automation."""
        result = _classify("encuentra restaurantes cerca")
        self.assertIn(result["intent"], ("automation", "information"))


# ═══════════════════════════════════════════════════════════════════════
# 4. Informative search → MUST be information with web_search, not automation
# ═══════════════════════════════════════════════════════════════════════

class TestInformativeSearchRouting(unittest.TestCase):
    """
    Queries like 'busca noticias sobre X' should go to information path
    (Tavily API), NOT automation (DuckDuckGo browser).
    """

    def test_busca_noticias_economia(self):
        result = _classify("busca noticias recientes sobre la economia")
        self.assertEqual(result["intent"], "information")
        self.assertTrue(result.get("needs_web_search"))

    def test_busca_informacion_clima(self):
        result = _classify("busca informacion sobre el cambio climatico")
        self.assertEqual(result["intent"], "information")
        self.assertTrue(result.get("needs_web_search"))

    def test_investiga_elecciones(self):
        """'investiga' without info indicators may go through CAPA 2 or automation."""
        result = _classify("investiga que paso con las elecciones")
        # Without specific info indicators, this may not hit Check 1.15
        # The important thing is it should NOT open DuckDuckGo blindly
        self.assertNotEqual(result.get("automation_intent"), "SEARCH")

    def test_busca_en_internet(self):
        result = _classify("busca en internet quien gano el partido")
        self.assertEqual(result["intent"], "information")
        self.assertTrue(result.get("needs_web_search"))

    def test_busca_ultimas_noticias(self):
        result = _classify("busca las ultimas noticias sobre inteligencia artificial")
        self.assertEqual(result["intent"], "information")
        self.assertTrue(result.get("needs_web_search"))


# ═══════════════════════════════════════════════════════════════════════
# 5. Conversational app mentions → NOT automation
# ═══════════════════════════════════════════════════════════════════════

class TestConversationalAppMentions(unittest.TestCase):
    """
    Mentioning an app name without a command verb should not trigger automation.
    """

    def test_navegador_alone(self):
        """Single word 'navegador' should NOT open anything."""
        result = _classify("navegador")
        self.assertNotEqual(result["intent"], "automation")

    def test_que_navegador_usas(self):
        result = _classify("que navegador usas")
        self.assertNotEqual(result["intent"], "automation")

    def test_me_gusta_firefox(self):
        result = _classify("me gusta firefox")
        self.assertNotEqual(result["intent"], "automation")

    def test_firefox_es_lento(self):
        result = _classify("firefox es lento ultimamente")
        self.assertNotEqual(result["intent"], "automation")


# ═══════════════════════════════════════════════════════════════════════
# 6. Existing conversational guards still work
# ═══════════════════════════════════════════════════════════════════════

class TestExistingGuardsStillWork(unittest.TestCase):
    """Verify existing conversational guards from before our changes."""

    def test_por_que_abriste(self):
        result = _classify("por que abriste duckduckgo")
        self.assertNotEqual(result["intent"], "automation")

    def test_no_te_pedi(self):
        result = _classify("no te pedi que abrieras eso")
        self.assertNotEqual(result["intent"], "automation")

    def test_ya_abriste_discord(self):
        result = _classify("ya abriste discord en firefox")
        self.assertNotEqual(result["intent"], "automation")

    def test_nunca_te_dije(self):
        result = _classify("nunca te dije que abrieras spotify")
        self.assertNotEqual(result["intent"], "automation")


if __name__ == "__main__":
    unittest.main()
