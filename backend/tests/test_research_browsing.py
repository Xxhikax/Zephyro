#!/usr/bin/env python3
"""
Unit tests for research-aware browsing: "entra/investiga en la página oficial de X ...".

Bug context (2026-06-14, STT call mode):
  "Entra a la página oficial de la ONPE e investiga la situación actual entre los
  candidatos..." opened raw browser tabs (onpe.gob.pe + a DuckDuckGo search) and never
  extracted nor wrote an answer. Root causes:
    (A) the BROWSE_AND_EXTRACT regex lacked the verb "investiga" and the "e" connector;
    (D) the "investiga ... sobre X" variant (a search verb, not a navigation verb) never
        reached BROWSE_AND_EXTRACT; STT also produced the typo "invstiga".

Fixes under test:
  - router CAPA 1: research verbs + "e" connector in BROWSE_AND_EXTRACT (1a) and a new
    research-on-named-site block that does not require navigation_hit (1b).
  - app_controller hybrid execution: when deep_browse fails / is empty / returns thin
    content, emit the __BROWSE_FALLBACK__ sentinel so the orchestrator can fall back to
    web search (Tavily) and still write an answer (2a).

The orchestrator's __BROWSE_FALLBACK__ → search_web_direct wiring (2b) reuses the exact
established _synth_fn + search_web_direct pattern already used elsewhere in orchestrator.py;
it is covered by manual end-to-end validation (heavy to mock in isolation).

All tests are pure-Python + mocks — they do NOT launch Edge (see .claude/rules/tests-safety.md):
AppController.__init__ is lazy (browser only spins up via the `browser` property, which the
BROWSE_AND_EXTRACT branch never touches) and deep_browse is mocked.

Usage:
  backend/venv/Scripts/python.exe -m pytest backend/tests/test_research_browsing.py -v
"""
import sys
import os
import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.router import IntentRouter


def _make_router() -> IntentRouter:
    """Router with a mocked LLM so CAPA 2 is never actually called for CAPA 1 matches."""
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent": "chat"}')
    return IntentRouter(mock_llm, context_manager=None)


def _classify(user_input: str) -> dict:
    return asyncio.run(_make_router().classify_and_parse(user_input))


# ═══════════════════════════════════════════════════════════════════════
# Routing — the research command must reach BROWSE_AND_EXTRACT
# ═══════════════════════════════════════════════════════════════════════

class TestResearchRouting(unittest.TestCase):

    def test_entra_e_investiga_onpe(self):
        """1a: 'entra a la página oficial de la ONPE e investiga …' (conector 'e')."""
        r = _classify(
            "Entra a la página oficial de la ONPE e investiga la situación actual "
            "entre los candidatos de Keiko Fujimori y Roberto Sánchez"
        )
        self.assertEqual(r.get("automation_intent"), "BROWSE_AND_EXTRACT")
        self.assertEqual(r.get("url"), "https://onpe.gob.pe")
        self.assertIn("situaci", r.get("extract_query", "").lower())

    def test_investiga_en_la_pagina_onpe(self):
        """1b: verbo de búsqueda + 'en la página oficial de la onpe sobre …'."""
        r = _classify("investiga en la página oficial de la onpe sobre las votaciones")
        self.assertEqual(r.get("automation_intent"), "BROWSE_AND_EXTRACT")
        self.assertEqual(r.get("url"), "https://onpe.gob.pe")
        self.assertIn("votaciones", r.get("extract_query", "").lower())

    def test_stt_typo_invstiga(self):
        """1b: el typo de STT 'invstiga' (sin la 'e') igual debe rutear."""
        r = _classify("invstiga en la pagina oficial de la onpe sobre las votaciones")
        self.assertEqual(r.get("automation_intent"), "BROWSE_AND_EXTRACT")
        self.assertEqual(r.get("url"), "https://onpe.gob.pe")

    def test_entra_wikipedia_dime_still_works(self):
        """Regresión 1a: el caso clásico 'entra a X y dime sobre Y' sigue funcionando."""
        r = _classify("entra a wikipedia y dime sobre la fotosíntesis")
        self.assertEqual(r.get("automation_intent"), "BROWSE_AND_EXTRACT")


# ═══════════════════════════════════════════════════════════════════════
# Regressions — the changes must NOT over-trigger on media/nav/chat
# ═══════════════════════════════════════════════════════════════════════

class TestRoutingRegressions(unittest.TestCase):

    def test_play_media_unchanged(self):
        r = _classify("reproduce una canción de Nicki Nicole")
        self.assertNotEqual(r.get("automation_intent"), "BROWSE_AND_EXTRACT")
        self.assertNotEqual(r.get("_source"), "capa1_research_site")

    def test_abre_instagram_unchanged(self):
        r = _classify("abre instagram")
        self.assertEqual(r.get("intent"), "automation")
        self.assertNotEqual(r.get("automation_intent"), "BROWSE_AND_EXTRACT")

    def test_named_page_without_topic_not_research(self):
        """Guard 1b: sin cláusula de tema explícita NO debe rutear a research_site
        (evita disparar con menciones conversacionales de un sitio)."""
        r = _classify("busca la página de facebook")
        self.assertNotEqual(r.get("_source"), "capa1_research_site")


# ═══════════════════════════════════════════════════════════════════════
# Hybrid execution — deep_browse thin/failed → __BROWSE_FALLBACK__ sentinel
# ═══════════════════════════════════════════════════════════════════════

class TestHybridBrowseExecution(unittest.IsolatedAsyncioTestCase):

    ANALYSIS = {
        "intent": "automation",
        "automation_intent": "BROWSE_AND_EXTRACT",
        "action": "browse_extract",
        "target": "onpe",
        "url": "https://onpe.gob.pe",
        "extract_query": "situación actual entre los candidatos",
    }

    async def _run(self, deep_browse_return: dict) -> str:
        from modules.automation.app_controller import AppController
        with patch("modules.search.web_search.WebSearch") as MockWS:
            MockWS.return_value.deep_browse = AsyncMock(return_value=deep_browse_return)
            ac = AppController()  # lazy: no browser is spun up for BROWSE_AND_EXTRACT
            return await ac.execute(dict(self.ANALYSIS))

    async def test_rich_content_synthesizes(self):
        rich = {"success": True, "content": "DATO " * 200, "title": "ONPE",
                "error": "", "error_type": ""}
        result = await self._run(rich)
        self.assertTrue(result.startswith("__BROWSE_EXTRACT__"))

    async def test_thin_content_falls_back(self):
        thin = {"success": True, "content": "Inicio Menú Contacto", "title": "ONPE",
                "error": "", "error_type": ""}
        result = await self._run(thin)
        self.assertTrue(result.startswith("__BROWSE_FALLBACK__"))
        self.assertIn("onpe", result)  # target hint travels in the sentinel

    async def test_browse_failure_falls_back(self):
        fail = {"success": False, "content": "", "title": "",
                "error": "timeout", "error_type": "timeout"}
        result = await self._run(fail)
        self.assertTrue(result.startswith("__BROWSE_FALLBACK__"))


if __name__ == "__main__":
    unittest.main()
