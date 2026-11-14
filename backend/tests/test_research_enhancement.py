#!/usr/bin/env python3
"""
Unit tests for the "fresh info" enhancement (v4): respond fast, then offer fresher
detail from the most authoritative source if it adds something novel.

Covers:
  - web_search.pick_best_source() — authoritative-source selection
  - web_search._parse_enhancement() / synthesize_enhancement() — novelty parsing
  - web_search._is_news_query() — the volatility gate (reused, sanity check)
  - Orchestrator._maybe_launch_enhancement() — race + offer append (volatile only)
  - Orchestrator._resolve_pending_extra_search() — follow-up yes/no handling

All pure-Python + mocks. No browser, no network, no LLM (NO Edge — tests-safety.md).
Orchestrator methods are exercised via Orchestrator.__new__ (skips the heavy __init__),
setting only the few attributes the methods touch.

Usage:
  backend/venv/Scripts/python.exe -m pytest backend/tests/test_research_enhancement.py -v
"""
import sys
import os
import time
import asyncio
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.search.web_search import (
    WebSearchResult, pick_best_source, _domain_authority,
    _parse_enhancement, synthesize_enhancement, _is_news_query,
)


def _r(url, score=0.0, date="", title="t"):
    return WebSearchResult(title=title, url=url, snippet="s", engine="tavily",
                           published_date=date, score=score)


# ═══════════════════════════════════════════════════════════════════════
# pick_best_source — authoritative-source selection
# ═══════════════════════════════════════════════════════════════════════

class TestPickBestSource(unittest.TestCase):

    def test_gov_beats_blog(self):
        results = [
            _r("https://random-blog.com/x", score=0.9, date="2026-06-14"),
            _r("https://onpe.gob.pe/resultados", score=0.4, date="2026-06-14"),
        ]
        best = pick_best_source(results)
        self.assertIsNotNone(best)
        self.assertIn("gob.pe", best.url)

    def test_media_beats_blog(self):
        results = [
            _r("https://random-blog.com/x", score=0.3),
            _r("https://elcomercio.pe/articulo", score=0.3, date="2026-06-14"),
        ]
        best = pick_best_source(results)
        self.assertIsNotNone(best)
        self.assertIn("elcomercio", best.url)

    def test_exclude_url(self):
        results = [_r("https://onpe.gob.pe/r", score=0.5)]
        self.assertIsNone(pick_best_source(results, exclude_url="https://onpe.gob.pe/r"))

    def test_below_threshold_returns_none(self):
        # blog sin autoridad, score bajo, sin fecha → 0.5*0.4 = 0.2 < 0.5
        results = [_r("https://blog.com/x", score=0.4)]
        self.assertIsNone(pick_best_source(results))

    def test_empty(self):
        self.assertIsNone(pick_best_source([]))

    def test_domain_authority(self):
        self.assertEqual(_domain_authority("https://onpe.gob.pe/x"), 1.0)
        self.assertEqual(_domain_authority("https://rpp.pe/x"), 0.7)
        self.assertEqual(_domain_authority("https://mi-blog.net/x"), 0.0)


# ═══════════════════════════════════════════════════════════════════════
# Enhancement synthesis / novelty parsing
# ═══════════════════════════════════════════════════════════════════════

class TestParseEnhancement(unittest.TestCase):

    def test_novel(self):
        raw = "NOVEDAD: SI\nCAMBIO: nuevos votos\nRESPUESTA: Keiko 50.05% vs Sánchez 49.95%."
        d = _parse_enhancement(raw)
        self.assertTrue(d["novel"])
        self.assertIn("Keiko", d["answer"])
        self.assertEqual(d["whats_new"], "nuevos votos")

    def test_not_novel(self):
        d = _parse_enhancement("NOVEDAD: NO\nCAMBIO:\nRESPUESTA:")
        self.assertFalse(d["novel"])

    def test_novel_without_answer_demoted(self):
        # marca novedad pero sin respuesta enriquecida → no hay nada útil que ofrecer
        d = _parse_enhancement("NOVEDAD: SI\nCAMBIO: algo\nRESPUESTA:")
        self.assertFalse(d["novel"])

    def test_empty(self):
        self.assertFalse(_parse_enhancement("")["novel"])


class TestSynthesizeEnhancement(unittest.IsolatedAsyncioTestCase):

    async def test_empty_content_skips_llm(self):
        called = []
        def fake(p):
            called.append(p)
            return "NOVEDAD: SI\nRESPUESTA: x"
        d = await synthesize_enhancement("q", "ans", "", "src", fake)
        self.assertFalse(d["novel"])
        self.assertEqual(called, [])  # no se llamó al LLM con contenido vacío

    async def test_novel(self):
        def fake(p):
            return "NOVEDAD: SI\nCAMBIO: datos más recientes\nRESPUESTA: Detalle al 98% escrutado."
        d = await synthesize_enhancement("q", "ans previa", "contenido con números", "ONPE", fake)
        self.assertTrue(d["novel"])
        self.assertIn("Detalle", d["answer"])


# ═══════════════════════════════════════════════════════════════════════
# Volatility gate (reused _is_news_query)
# ═══════════════════════════════════════════════════════════════════════

class TestNewsGate(unittest.TestCase):

    def test_volatile(self):
        self.assertTrue(_is_news_query("situación actual entre los candidatos"))
        self.assertTrue(_is_news_query("últimas noticias de bitcoin"))
        self.assertTrue(_is_news_query("qué pasó con las elecciones"))

    def test_static(self):
        self.assertFalse(_is_news_query("qué es la fotosíntesis"))
        self.assertFalse(_is_news_query("explicame la programación orientada a objetos"))


# ═══════════════════════════════════════════════════════════════════════
# Orchestrator methods (via __new__, no heavy init)
# ═══════════════════════════════════════════════════════════════════════

from core.orchestrator import Orchestrator


def _bare_orch():
    o = Orchestrator.__new__(Orchestrator)
    o._pending_extra_search_task = None
    o._pending_extra_search_ts = 0.0
    o.llm = MagicMock()
    o.console = MagicMock()
    return o


async def _done_task(result):
    async def _c():
        return result
    t = asyncio.create_task(_c())
    await t
    return t


class TestMaybeLaunchEnhancement(unittest.IsolatedAsyncioTestCase):

    async def test_static_query_no_offer(self):
        o = _bare_orch()
        out = await o._maybe_launch_enhancement("qué es la fotosíntesis", "La fotosíntesis es...")
        self.assertEqual(out, "La fotosíntesis es...")
        self.assertIsNone(o._pending_extra_search_task)

    async def test_volatile_novel_appends_offer(self):
        o = _bare_orch()
        async def fake_run(q, fa, fn):
            return {"novel": True, "answer": "Detalle X", "whats_new": "nuevos votos"}
        o._run_enhancement = fake_run
        out = await o._maybe_launch_enhancement("qué pasó con las elecciones", "Van así...")
        self.assertIn("¿Querés que te la muestre?", out)
        self.assertIn("nuevos votos", out)

    async def test_volatile_not_novel_no_offer(self):
        o = _bare_orch()
        async def fake_run(q, fa, fn):
            return {"novel": False, "answer": "", "whats_new": ""}
        o._run_enhancement = fake_run
        out = await o._maybe_launch_enhancement("últimas noticias de bitcoin", "Bitcoin está...")
        self.assertEqual(out, "Bitcoin está...")
        self.assertIsNone(o._pending_extra_search_task)


class TestResolvePendingExtraSearch(unittest.IsolatedAsyncioTestCase):

    async def test_affirm_returns_enhanced(self):
        o = _bare_orch()
        o._pending_extra_search_task = await _done_task(
            {"novel": True, "answer": "Detalle más actual: X.", "whats_new": "X"})
        o._pending_extra_search_ts = time.time()
        out = await o._resolve_pending_extra_search("sí, mostrame")
        self.assertIn("Detalle más actual", out)
        self.assertIsNone(o._pending_extra_search_task)

    async def test_affirm_not_novel_says_so(self):
        o = _bare_orch()
        o._pending_extra_search_task = await _done_task(
            {"novel": False, "answer": "", "whats_new": ""})
        o._pending_extra_search_ts = time.time()
        out = await o._resolve_pending_extra_search("dale")
        self.assertIn("no encontré nada más reciente", out.lower())

    async def test_negate_dismisses(self):
        o = _bare_orch()
        o._pending_extra_search_task = await _done_task({"novel": True, "answer": "X", "whats_new": ""})
        o._pending_extra_search_ts = time.time()
        out = await o._resolve_pending_extra_search("no")
        self.assertIsNotNone(out)
        self.assertIsNone(o._pending_extra_search_task)

    async def test_unrelated_message_passes_through(self):
        o = _bare_orch()
        o._pending_extra_search_task = await _done_task({"novel": True, "answer": "X", "whats_new": ""})
        o._pending_extra_search_ts = time.time()
        out = await o._resolve_pending_extra_search("qué hora es")
        self.assertIsNone(out)  # no es sí/no → procesar normal
        self.assertIsNone(o._pending_extra_search_task)

    async def test_ttl_expired(self):
        o = _bare_orch()
        o._pending_extra_search_task = await _done_task({"novel": True, "answer": "X", "whats_new": ""})
        o._pending_extra_search_ts = time.time() - 999
        out = await o._resolve_pending_extra_search("sí")
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
