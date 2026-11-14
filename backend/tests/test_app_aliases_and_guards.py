#!/usr/bin/env python3
"""
Tests for P1-P4 fix: APP_ALIASES, conversational guards, and supported_apps sync.

- T01-T08: APP_ALIASES resolve correctly (app_config)
- T10-T16: Subordinate clause guard neutralizes false positives
- T20-T32: All 22 native apps still route as automation
- T40-T43: supported_apps in self_state matches router apps
- T50-T52: Dynamic binary resolution (resolve_app_binary)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pytest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

_LINUX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Linux/KDE app names — Windows uses different binaries",
)

# Patch detect_intent and classify_by_rules before importing the router
async def _fake_detect_intent(text: str) -> str:
    return "command"

def _fake_classify_by_rules(text: str):
    return None

import core.intent_detector as _id_mod
_orig_detect_intent_aliases = _id_mod.detect_intent
_orig_classify_by_rules_aliases = _id_mod.classify_by_rules
_id_mod.detect_intent = _fake_detect_intent
_id_mod.classify_by_rules = _fake_classify_by_rules

from core.router import IntentRouter


def teardown_module(module):
    """Restore intent_detector globals after all tests in this module finish."""
    _id_mod.detect_intent = _orig_detect_intent_aliases
    _id_mod.classify_by_rules = _orig_classify_by_rules_aliases
from core.app_config import (
    APP_ALIASES, APP_COMMANDS, NATIVE_APPS, SUPPORTED_APPS,
    SITE_KEYWORDS, resolve_alias, resolve_app_binary,
)


def make_router():
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent":"automation"}')
    return IntentRouter(mock_llm, context_manager=None)


# ── T01-T08: APP_ALIASES ─────────────────────────────────────────────────────

class TestAppAliases(unittest.TestCase):
    """Verify APP_ALIASES map generic names to canonical binaries."""

    @_LINUX_ONLY
    def test_terminal_alias(self):
        self.assertEqual(APP_ALIASES["terminal"], "konsole")

    @_LINUX_ONLY
    def test_archivos_alias(self):
        self.assertEqual(APP_ALIASES["archivos"], "dolphin")

    def test_codigo_alias(self):
        self.assertEqual(APP_ALIASES["codigo"], "code")

    @_LINUX_ONLY
    def test_calculadora_alias(self):
        self.assertEqual(APP_ALIASES["calculadora"], "kcalc")

    @_LINUX_ONLY
    def test_monitor_alias(self):
        self.assertEqual(APP_ALIASES["monitor"], "plasma-systemmonitor")

    @_LINUX_ONLY
    def test_navegador_alias(self):
        self.assertEqual(APP_ALIASES["navegador"], "firefox")

    @_LINUX_ONLY
    def test_browser_alias(self):
        self.assertEqual(APP_ALIASES["browser"], "firefox")

    def test_vscode_alias(self):
        self.assertEqual(APP_ALIASES["vscode"], "code")


# ── T10-T16: Subordinate clause guard ────────────────────────────────────────

class TestSubordinateClauseGuard(IsolatedAsyncioTestCase):
    """Phrases with subordinate clauses mentioning 'abrir' must NOT trigger automation."""

    def setUp(self):
        self.router = make_router()

    async def test_para_que_puedas_abrir(self):
        r = await self.router.classify_and_parse(
            "pienso darte una skill para que puedas abrir apps de escritorio"
        )
        self.assertNotEqual(r.get("intent"), "automation",
                            f"Should be information, got {r}")

    async def test_capaz_de_abrir(self):
        r = await self.router.classify_and_parse(
            "quiero que seas capaz de abrir terminal y ejecutar comandos"
        )
        self.assertNotEqual(r.get("intent"), "automation",
                            f"Should be information, got {r}")

    async def test_la_idea_es_abrir(self):
        r = await self.router.classify_and_parse(
            "la idea es que puedas abrir apps como discord y spotify"
        )
        self.assertNotEqual(r.get("intent"), "automation",
                            f"Should be information, got {r}")

    async def test_planeo_abrir(self):
        r = await self.router.classify_and_parse(
            "planeo que puedas abrir archivos y el terminal por comodidad"
        )
        self.assertNotEqual(r.get("intent"), "automation",
                            f"Should be information, got {r}")

    async def test_skill_para_abrir(self):
        r = await self.router.classify_and_parse(
            "necesitas una skill para abrir aplicaciones de escritorio"
        )
        self.assertNotEqual(r.get("intent"), "automation",
                            f"Should be information, got {r}")

    async def test_habilidad_de_lanzar(self):
        r = await self.router.classify_and_parse(
            "te falta la habilidad de lanzar apps nativas como konsole"
        )
        self.assertNotEqual(r.get("intent"), "automation",
                            f"Should be information, got {r}")

    # Regression: simple imperative must STILL trigger automation
    async def test_abre_terminal_still_works(self):
        r = await self.router.classify_and_parse("abre terminal")
        self.assertEqual(r.get("intent"), "automation",
                         f"'abre terminal' should be automation, got {r}")

    async def test_abre_discord_still_works(self):
        r = await self.router.classify_and_parse("abre discord")
        self.assertEqual(r.get("intent"), "automation",
                         f"'abre discord' should be automation, got {r}")


# ── T20-T32: All native apps route as automation ─────────────────────────────

class TestAllNativeAppsRoute(IsolatedAsyncioTestCase):
    """Every app in NATIVE_APPS must be routed as automation with 'abre <app>'."""

    def setUp(self):
        self.router = make_router()

    async def _check_app(self, app_name: str):
        r = await self.router.classify_and_parse(f"abre {app_name}")
        self.assertEqual(
            r.get("intent"), "automation",
            f"'abre {app_name}' should be automation, got intent={r.get('intent')}"
        )

    async def test_firefox(self):    await self._check_app("firefox")
    async def test_browser(self):    await self._check_app("browser")
    async def test_navegador(self):  await self._check_app("navegador")
    async def test_chrome(self):     await self._check_app("chrome")
    async def test_spotify(self):    await self._check_app("spotify")
    async def test_terminal(self):   await self._check_app("terminal")
    async def test_kitty(self):      await self._check_app("kitty")
    async def test_code(self):       await self._check_app("code")
    async def test_codigo(self):     await self._check_app("codigo")
    async def test_archivos(self):   await self._check_app("archivos")
    async def test_dolphin(self):    await self._check_app("dolphin")
    async def test_calculadora(self): await self._check_app("calculadora")
    async def test_monitor(self):    await self._check_app("monitor")
    async def test_vscode(self):     await self._check_app("vscode")
    async def test_discord(self):    await self._check_app("discord")
    async def test_vlc(self):        await self._check_app("vlc")
    async def test_steam(self):      await self._check_app("steam")
    async def test_libreoffice(self): await self._check_app("libreoffice")
    async def test_konsole(self):    await self._check_app("konsole")
    async def test_alacritty(self):  await self._check_app("alacritty")
    async def test_kate(self):       await self._check_app("kate")


# ── T40-T43: supported_apps sync ─────────────────────────────────────────────

class TestSupportedAppsSync(unittest.TestCase):
    """Verify that SUPPORTED_APPS covers all canonical native apps."""

    def test_all_canonical_apps_in_supported(self):
        """Every canonical (non-alias) native app should be in SUPPORTED_APPS."""
        # Canonical apps = items in NATIVE_APPS that are NOT keys in APP_ALIASES
        # (aliases like 'terminal' should also be in SUPPORTED_APPS for LLM knowledge)
        canonical = {a for a in NATIVE_APPS if a not in APP_ALIASES}
        missing = canonical - set(SUPPORTED_APPS)
        self.assertEqual(missing, set(),
                         f"Missing from SUPPORTED_APPS: {missing}")

    def test_aliases_in_supported_for_llm(self):
        """Key aliases (terminal, archivos, calculadora, monitor) should be in SUPPORTED_APPS
        so the LLM knows Zephyro can handle these names."""
        key_aliases = ["terminal", "archivos", "calculadora", "monitor", "vscode", "codigo"]
        missing = [a for a in key_aliases if a not in SUPPORTED_APPS]
        self.assertEqual(missing, [],
                         f"Aliases missing from SUPPORTED_APPS: {missing}")

    def test_supported_count_minimum(self):
        """SUPPORTED_APPS should have at least 21 entries (old 14 was insufficient)."""
        self.assertGreaterEqual(len(SUPPORTED_APPS), 21,
                                f"SUPPORTED_APPS only has {len(SUPPORTED_APPS)} items")


# ── T50-T52: Dynamic binary resolution ───────────────────────────────────────

class TestDynamicResolution(unittest.TestCase):
    """Test resolve_app_binary and resolve_alias."""

    def test_resolve_alias_terminal(self):
        """resolve_alias('terminal') should return a real binary name, not 'terminal'."""
        result = resolve_alias("terminal")
        # It should be one of the fallback candidates, not 'terminal' itself
        self.assertNotEqual(result, "terminal",
                            "terminal should resolve to a real binary")

    def test_resolve_alias_passthrough(self):
        """Unknown app names pass through unchanged."""
        result = resolve_alias("someunknownapp")
        self.assertEqual(result, "someunknownapp")

    @_LINUX_ONLY
    def test_app_commands_has_expanded_apps(self):
        """APP_COMMANDS should include konsole, code, steam, etc."""
        expected = ["konsole", "code", "steam", "libreoffice", "kcalc",
                    "plasma-systemmonitor", "kitty", "alacritty"]
        for app in expected:
            self.assertIn(app, APP_COMMANDS,
                          f"{app} missing from APP_COMMANDS")


# ── T60: Router alias_map resolves targets ───────────────────────────────────

class TestRouterAliasResolution(IsolatedAsyncioTestCase):
    """Router should resolve aliases in the target field."""

    def setUp(self):
        self.router = make_router()

    @_LINUX_ONLY
    async def test_terminal_resolves_to_konsole(self):
        r = await self.router.classify_and_parse("abre terminal")
        # The router's alias_map should resolve "terminal" → "konsole"
        target = r.get("target", "")
        self.assertEqual(target, "konsole",
                         f"Expected target='konsole', got '{target}'")

    @_LINUX_ONLY
    async def test_archivos_resolves_to_dolphin(self):
        r = await self.router.classify_and_parse("abre archivos")
        target = r.get("target", "")
        self.assertEqual(target, "dolphin",
                         f"Expected target='dolphin', got '{target}'")

    @_LINUX_ONLY
    async def test_calculadora_resolves_to_kcalc(self):
        r = await self.router.classify_and_parse("abre calculadora")
        target = r.get("target", "")
        self.assertEqual(target, "kcalc",
                         f"Expected target='kcalc', got '{target}'")

    @_LINUX_ONLY
    async def test_monitor_resolves(self):
        r = await self.router.classify_and_parse("abre monitor")
        target = r.get("target", "")
        self.assertEqual(target, "plasma-systemmonitor",
                         f"Expected target='plasma-systemmonitor', got '{target}'")

    async def test_codigo_resolves_to_code(self):
        r = await self.router.classify_and_parse("abre codigo")
        target = r.get("target", "")
        self.assertEqual(target, "code",
                         f"Expected target='code', got '{target}'")


if __name__ == "__main__":
    unittest.main()
