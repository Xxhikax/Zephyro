#!/usr/bin/env python3
"""
Post-fix QA: Automation routing for web apps, aliases, and compound commands.
Tests classify_and_parse() directly — no network, no LLM calls.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

# Patch detect_intent and classify_by_rules so no Haiku API calls are made.
#
# IMPORTANT: patch in setUpModule / tearDownModule, NOT at import time.
# Patching at import time runs during pytest COLLECTION (before any test of any
# file runs) and clobbers the globals for the whole session — polluting
# unrelated files like test_verb_consistency.py that exercise the real detector.
#
# NOTE: router does `from core.intent_detector import detect_intent, ...`, so it
# binds those names into ITS OWN namespace at import. We therefore patch the
# names on `core.router` (where they're actually looked up at call time), not on
# core.intent_detector. setUpModule scopes the patch to this module's run only.
async def _fake_detect_intent(text: str) -> str:
    return "command"

def _fake_classify_by_rules(text: str):
    return None  # Let CAPA 1 heuristics do the work

import pytest

import core.router as _router_mod
from core.router import IntentRouter

_orig_detect_intent = None
_orig_classify_by_rules = None


def setUpModule():
    """Install the fakes on the router namespace only while this module runs."""
    global _orig_detect_intent, _orig_classify_by_rules
    _orig_detect_intent = _router_mod.detect_intent
    _orig_classify_by_rules = _router_mod.classify_by_rules
    _router_mod.detect_intent = _fake_detect_intent
    _router_mod.classify_by_rules = _fake_classify_by_rules


def tearDownModule():
    """Restore router globals after this module's tests finish."""
    if _orig_detect_intent is not None:
        _router_mod.detect_intent = _orig_detect_intent
    if _orig_classify_by_rules is not None:
        _router_mod.classify_by_rules = _orig_classify_by_rules


def make_router():
    """Return a router with a dummy LLM and no context manager."""
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent":"automation","action":"open","target":"firefox"}')
    return IntentRouter(mock_llm, context_manager=None)


# ─── Helpers ──────────────────────────────────────────────────────────────────

PASS = "✓"
FAIL = "✗"

results: list[tuple[str, str, str]] = []  # (label, status, note)


def record(label: str, ok: bool, note: str = ""):
    status = PASS if ok else FAIL
    results.append((label, status, note))
    icon = "\033[92m✓\033[0m" if ok else "\033[91m✗\033[0m"
    print(f"  {icon} {label}" + (f"  — {note}" if note else ""))


# ─── Test classes ─────────────────────────────────────────────────────────────

class TestWebAppsBasicas(IsolatedAsyncioTestCase):
    """Web apps básicas: router must return automation + NAVIGATE + correct target."""

    def setUp(self):
        self.router = make_router()

    async def _check(self, command: str, expected_target: str, label: str):
        r = await self.router.classify_and_parse(command)
        ok = (
            r.get("intent") == "automation"
            and r.get("automation_intent") == "NAVIGATE"
            and r.get("target") == expected_target
        )
        note = f"got intent={r.get('intent')} ai={r.get('automation_intent')} target={r.get('target')!r}"
        record(label, ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_youtube(self):
        await self._check("abre youtube", "youtube", "T01 abre youtube")

    async def test_twitter(self):
        await self._check("abre twitter", "twitter", "T02 abre twitter")

    async def test_instagram(self):
        await self._check("abre instagram", "instagram", "T03 abre instagram")

    async def test_spotify(self):
        await self._check("abre spotify", "spotify", "T04 abre spotify")

    async def test_whatsapp(self):
        await self._check("abre whatsapp", "whatsapp", "T05 abre whatsapp")

    async def test_gmail(self):
        await self._check("abre gmail", "gmail", "T06 abre gmail")

    async def test_github(self):
        await self._check("abre github", "github", "T07 abre github")

    async def test_reddit(self):
        await self._check("abre reddit", "reddit", "T08 abre reddit")

    async def test_netflix(self):
        await self._check("abre netflix", "netflix", "T09 abre netflix")

    async def test_discord(self):
        await self._check("abre discord", "discord", "T10 abre discord")

    async def test_tiktok(self):
        await self._check("abre tiktok", "tiktok", "T11 abre tiktok")

    async def test_linkedin(self):
        await self._check("abre linkedin", "linkedin", "T12 abre linkedin")


class TestAliases(IsolatedAsyncioTestCase):
    """Aliases: X→twitter, ig→instagram, yt→youtube, wa→whatsapp."""

    def setUp(self):
        self.router = make_router()

    async def _check_alias(self, command: str, expected_target: str, label: str):
        r = await self.router.classify_and_parse(command)
        ok = (
            r.get("intent") == "automation"
            and r.get("target") == expected_target
        )
        note = f"got target={r.get('target')!r} intent={r.get('intent')}"
        record(label, ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_alias_x_to_twitter(self):
        await self._check_alias("abre X", "twitter", "T13 alias X→twitter")

    async def test_alias_ig_to_instagram(self):
        await self._check_alias("abre ig", "instagram", "T14 alias ig→instagram")

    async def test_alias_yt_to_youtube(self):
        await self._check_alias("abre yt", "youtube", "T15 alias yt→youtube")

    async def test_alias_wa_to_whatsapp(self):
        await self._check_alias("abre wa", "whatsapp", "T16 alias wa→whatsapp")


class TestVariacionesDeFrase(IsolatedAsyncioTestCase):
    """Phrase variations must still route to automation+NAVIGATE for web apps."""

    def setUp(self):
        self.router = make_router()

    async def _check(self, command: str, expected_target: str, label: str):
        r = await self.router.classify_and_parse(command)
        ok = r.get("intent") == "automation" and r.get("target") == expected_target
        note = f"got intent={r.get('intent')} target={r.get('target')!r}"
        record(label, ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_entra_a_youtube(self):
        await self._check("entra a youtube", "youtube", "T17 entra a youtube")

    async def test_navega_a_youtube(self):
        await self._check("navega a youtube.com", "youtube.com", "T18 navega a youtube.com")

    async def test_ve_a_instagram(self):
        await self._check("ve a instagram", "instagram", "T19 ve a instagram")

    async def test_visita_reddit(self):
        await self._check("visita reddit", "reddit", "T20 visita reddit")


class TestPlayMedia(IsolatedAsyncioTestCase):
    """PLAY_MEDIA intent for reproduce/pon commands."""

    def setUp(self):
        self.router = make_router()

    async def _check_play(self, command: str, expected_provider: str, expected_query: str, label: str):
        r = await self.router.classify_and_parse(command)
        ok = (
            r.get("intent") == "automation"
            and r.get("automation_intent") == "PLAY_MEDIA"
            and r.get("provider") == expected_provider
            and r.get("search_query") == expected_query
        )
        note = f"ai={r.get('automation_intent')} prov={r.get('provider')} sq={r.get('search_query')!r}"
        record(label, ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_reproduce_youtube(self):
        await self._check_play(
            "reproduce Never Gonna Give You Up en youtube",
            "youtube",
            "never gonna give you up",
            "T21 reproduce en youtube"
        )

    async def test_pon_musica(self):
        r = await self.router.classify_and_parse("pon música en youtube")
        ok = r.get("intent") == "automation" and r.get("automation_intent") == "PLAY_MEDIA"
        note = f"ai={r.get('automation_intent')}"
        record("T22 pon música en youtube", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_reproduce_youtube_music(self):
        r = await self.router.classify_and_parse("reproduce lo-fi en youtube music")
        ok = (
            r.get("intent") == "automation"
            and r.get("automation_intent") == "PLAY_MEDIA"
            and r.get("provider") == "youtube_music"
        )
        note = f"prov={r.get('provider')}"
        record("T23 reproduce en youtube music", ok, "" if ok else note)
        self.assertTrue(ok, note)


class TestComandosCompuestos(IsolatedAsyncioTestCase):
    """Compound commands: 'abre [app] y [acción]'."""

    def setUp(self):
        self.router = make_router()

    async def test_instagram_y_reels(self):
        r = await self.router.classify_and_parse("abre instagram y entra a reels")
        ok = (
            r.get("intent") == "automation"
            and r.get("automation_intent") == "NAVIGATE"
            and r.get("target") == "instagram"
            and r.get("sub_path") == "reels"
        )
        note = f"target={r.get('target')!r} sub_path={r.get('sub_path')!r} ai={r.get('automation_intent')}"
        record("T24 instagram y entra a reels", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_youtube_y_busca(self):
        r = await self.router.classify_and_parse("abre youtube y busca lo-fi music")
        # Accepts NAVIGATE (navigate_target/sub_path) or PLAY_MEDIA (search_query) —
        # both correctly open YouTube and search for the query.
        ok = (
            r.get("intent") == "automation"
            and r.get("automation_intent") in ("NAVIGATE", "PLAY_MEDIA")
            and r.get("target") == "youtube"
            and ("navigate_target" in r or "sub_path" in r or "search_query" in r)
        )
        note = (f"ai={r.get('automation_intent')!r} target={r.get('target')!r} "
                f"nt={r.get('navigate_target')!r} sp={r.get('sub_path')!r} sq={r.get('search_query')!r}")
        record("T25 youtube y busca lo-fi", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_reddit_y_subred(self):
        r = await self.router.classify_and_parse("abre reddit y entra a r/programming")
        ok = (
            r.get("intent") == "automation"
            and r.get("automation_intent") == "NAVIGATE"
            and r.get("target") == "reddit"
            and "programming" in str(r.get("sub_path", ""))
        )
        note = f"target={r.get('target')!r} sub_path={r.get('sub_path')!r}"
        record("T26 reddit y r/programming", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_github_y_repos(self):
        r = await self.router.classify_and_parse("abre github y entra a mis repos")
        ok = (
            r.get("intent") == "automation"
            and r.get("target") == "github"
        )
        note = f"target={r.get('target')!r} intent={r.get('intent')}"
        record("T27 github y mis repos", ok, "" if ok else note)
        self.assertTrue(ok, note)


class TestEdgeCases(IsolatedAsyncioTestCase):
    """Edge cases: empty input, unknown app, uppercase."""

    def setUp(self):
        self.router = make_router()

    async def test_empty_input(self):
        r = await self.router.classify_and_parse("   ")
        # Must not crash; should be information or anything sensible
        ok = isinstance(r, dict) and "intent" in r
        record("T28 input vacío", ok, f"got={r}")
        self.assertTrue(ok)

    async def test_unknown_app(self):
        r = await self.router.classify_and_parse("abre miapppersonalxyz")
        # Should be automation with open action (app or unknown target)
        ok = r.get("intent") == "automation"
        note = f"intent={r.get('intent')}"
        record("T29 app inexistente", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_uppercase_twitter(self):
        r = await self.router.classify_and_parse("ABRE TWITTER")
        ok = r.get("intent") == "automation" and r.get("target") == "twitter"
        note = f"intent={r.get('intent')} target={r.get('target')!r}"
        record("T30 ABRE TWITTER (mayúsculas)", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_uppercase_x_alias(self):
        r = await self.router.classify_and_parse("abre X")
        ok = r.get("intent") == "automation" and r.get("target") == "twitter"
        note = f"target={r.get('target')!r}"
        record("T31 abre X (alias uppercase)", ok, "" if ok else note)
        self.assertTrue(ok, note)


class TestNativeBrowser(IsolatedAsyncioTestCase):
    """Native browser opens (not web app)."""

    def setUp(self):
        self.router = make_router()

    async def test_open_firefox(self):
        r = await self.router.classify_and_parse("abre firefox")
        ok = r.get("intent") == "automation" and r.get("target") == "firefox"
        note = f"intent={r.get('intent')} target={r.get('target')!r}"
        record("T32 abre firefox (nativo)", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_open_browser(self):
        r = await self.router.classify_and_parse("abre el navegador")
        # On Windows, "navegador" resolves to msedge/edge; on Linux to firefox.
        ok = r.get("intent") == "automation" and r.get("target") in (
            "firefox", "navegador", "browser", "msedge", "edge"
        )
        note = f"target={r.get('target')!r}"
        record("T33 abre el navegador", ok, "" if ok else note)
        self.assertTrue(ok, note)


class TestNavSubpathOpenVerb(IsolatedAsyncioTestCase):
    """Sub-path con verbo de APERTURA: "abre Reels de Instagram".

    Regresión real: navigation_triggers solo tenía "abre la página de ", así que
    navigation_hit era False y el bloque nav-subpath no corría → se perdía
    "reels" y se navegaba a la home (instagram.com en vez de /reels).
    """

    def setUp(self):
        self.router = make_router()

    async def _sub_path_for(self, text):
        r = await self.router.classify_and_parse(text)
        return r.get("intent"), r.get("target"), r.get("sub_path")

    async def test_abre_reels_de_instagram(self):
        intent, target, sub = await self._sub_path_for("abre reels de instagram")
        ok = intent == "automation" and target == "instagram" and sub == "reels"
        note = f"intent={intent} target={target!r} sub_path={sub!r}"
        record("T34 abre reels de instagram (sub_path)", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_abre_reels_capitalizado(self):
        intent, target, sub = await self._sub_path_for("Abre Reels de Instagram")
        ok = target == "instagram" and sub == "reels"
        note = f"target={target!r} sub_path={sub!r}"
        record("T35 Abre Reels de Instagram (capitalizado)", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_voseo_abrime(self):
        intent, target, sub = await self._sub_path_for("abrime reels de instagram")
        ok = target == "instagram" and sub == "reels"
        note = f"target={target!r} sub_path={sub!r}"
        record("T36 abrime reels de instagram (voseo)", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_verbo_navegacion_sigue_funcionando(self):
        intent, target, sub = await self._sub_path_for("entra a reels de instagram")
        ok = target == "instagram" and sub == "reels"
        note = f"target={target!r} sub_path={sub!r}"
        record("T37 entra a reels (no regresión)", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_pagina_generica_no_es_subpath(self):
        """"abre la página de instagram" pide la HOME, no instagram.com/la pagina."""
        intent, target, sub = await self._sub_path_for("abre la pagina de instagram")
        ok = target == "instagram" and not sub
        note = f"target={target!r} sub_path={sub!r}"
        record("T38 'la pagina de X' → home, sin sub_path", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_sitio_generico_no_es_subpath(self):
        intent, target, sub = await self._sub_path_for("abre el sitio de youtube")
        ok = not sub
        note = f"target={target!r} sub_path={sub!r}"
        record("T39 'el sitio de X' → home, sin sub_path", ok, "" if ok else note)
        self.assertTrue(ok, note)

    async def test_apertura_simple_sin_subpath(self):
        """"abre instagram" no debe inventar sub_path."""
        intent, target, sub = await self._sub_path_for("abre instagram")
        ok = target == "instagram" and not sub
        note = f"target={target!r} sub_path={sub!r}"
        record("T40 abre instagram (sin sub_path)", ok, "" if ok else note)
        self.assertTrue(ok, note)


# ─── Summary ──────────────────────────────────────────────────────────────────

def print_summary():
    total = len(results)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = total - passed
    print("\n" + "=" * 60)
    print(f" RESULTADO FINAL: {passed}/{total} PASSED  ({failed} FAILED)")
    print("=" * 60)
    if failed:
        print("\n[FALLOS]")
        for label, status, note in results:
            if status == FAIL:
                print(f"  ✗ {label}")
                if note:
                    print(f"    → {note}")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print(" QA: Automation Routing Post-Fix")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestWebAppsBasicas,
        TestAliases,
        TestVariacionesDeFrase,
        TestPlayMedia,
        TestComandosCompuestos,
        TestEdgeCases,
        TestNativeBrowser,
        TestNavSubpathOpenVerb,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, 'w'))
    result = runner.run(suite)
    print_summary()
    sys.exit(0 if result.wasSuccessful() else 1)
