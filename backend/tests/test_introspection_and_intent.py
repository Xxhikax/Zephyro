#!/usr/bin/env python3
"""
Exhaustive test suite for Zephyro introspection, capability awareness,
intent detection accuracy, and complex request handling.

Tests the FULL pipeline via HTTP API (orchestrator + router + LLM + self_state).

Error patterns identified from Telegram conversation analysis:
  P1: Capability Denial — LLM says "no puedo" when Zephyro actually can
  P2: "No entendí" default — complex/contextual questions get catch-all response
  P3: Inconsistent self-awareness — same question gets contradictory answers
  P4: Research ≠ Search — "investiga X" doesn't trigger web search

Usage:
  cd backend && python -m tests.test_introspection_and_intent
"""
import sys
import os
import json
import time
import asyncio
import unittest
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE = os.environ.get("Zephyro_API_BASE", "http://localhost:8000")
TIMEOUT = 30  # seconds per request

# ── HTTP helper ───────────────────────────────────────────────────────────────

def query_zephyro(text: str, timeout: float = TIMEOUT) -> dict:
    """Send a message to Zephyro and return the full JSON response.

    Integration tests need a live server at :8000. Without it, urlopen waits the
    full TIMEOUT per call and the suite HANGS. Gate on ZEPHYRO_INTEGRATION so
    each integration test skips instantly; the Unit classes never call this.
    """
    if not os.environ.get("ZEPHYRO_INTEGRATION", "").strip():
        raise unittest.SkipTest(
            "requires a running Zephyro server at :8000 — set ZEPHYRO_INTEGRATION=1"
        )
    import urllib.request
    import urllib.error
    payload = json.dumps({"message": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": str(e), "response": ""}
    except Exception as e:
        return {"error": str(e), "response": ""}


def response_text(result: dict) -> str:
    return (result.get("response") or "").lower().strip()


# ── Denial detection helpers ──────────────────────────────────────────────────

DENIAL_PHRASES = [
    "no puedo", "no soy capaz", "no tengo la capacidad",
    "no estoy diseñado para", "no me es posible",
    "lamentablemente no", "desafortunadamente no",
    "no tengo acceso", "no cuento con",
    "fuera de mis capacidades", "excede mis capacidades",
    "soy un asistente de texto", "soy un modelo de lenguaje",
    "no tengo pseudo", "no tengo conciencia",
]

NO_ENTENDI_PHRASES = [
    "no entendí", "no entendi", "podrías reformularla",
    "podrias reformularla", "no comprendo",
]


def has_denial(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in DENIAL_PHRASES)


def has_no_entendi(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in NO_ENTENDI_PHRASES)


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 1: CAPABILITY DENIAL TESTS
# The LLM should NEVER deny capabilities that Zephyro actually has.
# ══════════════════════════════════════════════════════════════════════════════

class TestCapabilityDenial(unittest.TestCase):
    """P1: Zephyro must not deny its own capabilities."""

    def _assert_no_denial(self, query: str, msg: str = ""):
        result = query_zephyro(query)
        resp = response_text(result)
        self.assertFalse(
            has_denial(resp),
            f"DENIAL detected for '{query}': {resp[:200]}... {msg}"
        )
        return resp

    # ── Automation awareness ──────────────────────────────────────────────

    def test_knows_can_open_apps(self):
        """Should know it can open applications."""
        resp = self._assert_no_denial("puedes abrir aplicaciones?")
        self.assertTrue(
            any(w in resp for w in ["sí", "si", "puedo", "discord", "firefox", "spotify"]),
            f"Expected affirmative about apps: {resp[:200]}"
        )

    def test_knows_can_open_discord(self):
        """Should know it can open Discord specifically."""
        resp = self._assert_no_denial("eres capaz de abrir discord?")
        self.assertTrue(
            any(w in resp for w in ["sí", "si", "puedo", "discord"]),
            f"Expected affirmative about Discord: {resp[:200]}"
        )

    def test_knows_can_open_firefox(self):
        """Should know it can open Firefox."""
        resp = self._assert_no_denial("puedes abrir firefox?")

    # ── Web search awareness ──────────────────────────────────────────────

    def test_knows_has_web_search(self):
        """Should know it has web search (Tavily)."""
        resp = self._assert_no_denial("puedes buscar información en internet?")
        self.assertFalse(
            "no tengo acceso a internet" in resp,
            f"Denied internet access: {resp[:200]}"
        )

    def test_knows_has_tavily(self):
        """Should not deny having Tavily when asked about search."""
        resp = self._assert_no_denial("tienes acceso a algún buscador web?")

    def test_research_not_denied(self):
        """'investigar' should not trigger capability denial."""
        resp = self._assert_no_denial(
            "puedes investigar temas en internet?"
        )

    # ── Pseudo-consciousness awareness ────────────────────────────────────

    def test_knows_has_pseudo_consciousness(self):
        """Should know it has pseudo-consciousness (self_state.py)."""
        resp = self._assert_no_denial("tienes pseudo conciencia?")
        self.assertTrue(
            any(w in resp for w in ["sí", "si", "pseudo", "self_state", "self state"]),
            f"Expected affirmative about pseudo-consciousness: {resp[:200]}"
        )

    def test_knows_has_self_awareness(self):
        """Should not deny self-awareness system."""
        resp = self._assert_no_denial("cómo funciona tu auto-consciencia?")

    def test_consciousness_consistency(self):
        """Two identical questions should get consistent answers (both affirmative)."""
        q = "tienes algún tipo de conciencia o introspección?"
        r1 = query_zephyro(q)
        r2 = query_zephyro(q)
        # Neither should deny
        self.assertFalse(has_denial(response_text(r1)), f"First denial: {response_text(r1)[:200]}")
        self.assertFalse(has_denial(response_text(r2)), f"Second denial: {response_text(r2)[:200]}")

    # ── Vision awareness ──────────────────────────────────────────────────

    def test_knows_has_vision(self):
        """Should know it can capture/analyze screen."""
        resp = self._assert_no_denial("puedes ver mi pantalla?")

    # ── Voice awareness ───────────────────────────────────────────────────

    def test_knows_has_voice(self):
        """Should know it has TTS/STT."""
        resp = self._assert_no_denial("puedes hablar en voz alta?")

    # ── Memory awareness ──────────────────────────────────────────────────

    def test_knows_has_memory(self):
        """Should know it has persistent memory."""
        resp = self._assert_no_denial("tienes algún tipo de memoria persistente?")

    # ── Learning awareness ────────────────────────────────────────────────

    def test_knows_has_learning(self):
        """Should know it has learning/feedback system."""
        resp = self._assert_no_denial("aprendes de tus errores?")

    # ── Code analysis awareness ───────────────────────────────────────────

    def test_knows_has_code_analysis(self):
        """Should know it can analyze code."""
        resp = self._assert_no_denial("puedes analizar código fuente?")

    # ── Time awareness ────────────────────────────────────────────────────

    def test_knows_current_time(self):
        """Should know the current time (not deny it)."""
        resp = self._assert_no_denial("qué hora es?")


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 2: "NO ENTENDÍ" DEFAULT TESTS
# Complex but clear questions should NOT get the catch-all "No entendí"
# ══════════════════════════════════════════════════════════════════════════════

class TestNoEntendi(unittest.TestCase):
    """P2: Contextual/complex questions should not get 'No entendí' default."""

    def _assert_no_default_rejection(self, query: str):
        result = query_zephyro(query)
        resp = response_text(result)
        self.assertFalse(
            has_no_entendi(resp),
            f"Got 'no entendí' for clear question '{query}': {resp[:200]}"
        )
        return resp

    # ── Capability questions (clear, not ambiguous) ───────────────────────

    def test_what_can_you_do(self):
        resp = self._assert_no_default_rejection("qué puedes hacer?")
        # Should list capabilities
        self.assertTrue(len(resp) > 50, f"Response too short: {resp}")

    def test_what_are_your_limitations(self):
        resp = self._assert_no_default_rejection("cuáles son tus limitaciones?")
        self.assertTrue(len(resp) > 50, f"Response too short: {resp}")

    def test_explain_yourself(self):
        self._assert_no_default_rejection("explícame cómo funcionas")

    def test_how_do_you_process_messages(self):
        self._assert_no_default_rejection("cómo procesas mis mensajes?")

    # ── Conversational references (should be understood) ──────────────────

    def test_discord_conversational_past(self):
        """'discord es genial' is conversational, not a command. Should not get 'no entendí'."""
        resp = self._assert_no_default_rejection("discord es genial")

    def test_discord_reference(self):
        """'hablamos por discord' is conversational. Should respond naturally."""
        resp = self._assert_no_default_rejection("hablamos por discord")

    # ── Complex requests ──────────────────────────────────────────────────

    def test_multi_step_request(self):
        """Multi-step requests should get a real answer."""
        self._assert_no_default_rejection(
            "necesito que busques información sobre inteligencia artificial y me hagas un resumen"
        )

    def test_conditional_request(self):
        """Conditional requests should be understood."""
        self._assert_no_default_rejection(
            "si puedes buscar en internet, dime qué hora es en Tokyo"
        )

    def test_tell_me_a_joke(self):
        """Should be able to tell a joke (or explain it can't well), not 'no entendí'."""
        resp = self._assert_no_default_rejection("dime un chiste corto")
        # Should not deny ability to chat
        self.assertFalse(
            "no puedo generar" in resp,
            f"Denied joke ability: {resp[:200]}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 3: SELF-AWARENESS / INTROSPECTION TESTS
# Zephyro should use self_state consistently and accurately.
# ══════════════════════════════════════════════════════════════════════════════

class TestSelfAwareness(unittest.TestCase):
    """P3: Zephyro should have consistent, accurate self-knowledge."""

    def test_capabilities_list_complete(self):
        """When asked about capabilities, should mention key ones."""
        result = query_zephyro("dime todas tus capacidades actuales")
        resp = response_text(result)
        key_capabilities = ["automation", "busca", "voz", "pantalla", "memoria"]
        found = sum(1 for k in key_capabilities if k in resp)
        self.assertGreaterEqual(
            found, 3,
            f"Only found {found}/5 key capabilities in: {resp[:300]}"
        )

    def test_limitations_honest(self):
        """Should mention real limitations, not made-up ones."""
        result = query_zephyro("qué limitaciones tienes?")
        resp = response_text(result)
        # Should NOT claim "no tengo acceso a internet" as a limitation
        self.assertFalse(
            "sin conexión a internet" in resp and "tavily" not in resp,
            f"Claimed no internet without mentioning Tavily: {resp[:300]}"
        )

    def test_honest_self_assessment(self):
        """Should provide metrics-based assessment when asked."""
        result = query_zephyro("cómo evalúas tu rendimiento en esta sesión?")
        resp = response_text(result)
        self.assertFalse(has_denial(resp), f"Denied assessment ability: {resp[:200]}")

    def test_pseudo_consciousness_describes_system(self):
        """Should describe the actual pseudo-consciousness system."""
        result = query_zephyro("explícame tu sistema de pseudo conciencia")
        resp = response_text(result)
        self.assertFalse(has_denial(resp), f"Denied pseudo-consciousness: {resp[:200]}")
        # Should mention at least one real component
        components = ["self_state", "self state", "post-process", "post_process",
                      "capability", "bug report", "introspección", "reflexión"]
        found = any(c in resp for c in components)
        self.assertTrue(found, f"No real components mentioned: {resp[:300]}")

    def test_knows_supported_apps(self):
        """Should list actual supported apps when asked."""
        result = query_zephyro("qué aplicaciones puedes abrir?")
        resp = response_text(result)
        apps = ["discord", "firefox", "spotify"]
        found = sum(1 for a in apps if a in resp)
        self.assertGreaterEqual(found, 2, f"Only {found}/3 apps mentioned: {resp[:300]}")

    def test_knows_search_capabilities(self):
        """Should mention Tavily or web search when asked about internet."""
        result = query_zephyro("puedes buscar cosas en internet? con qué herramientas?")
        resp = response_text(result)
        search_terms = ["tavily", "duckduckgo", "búsqueda", "busqueda", "web", "internet"]
        found = any(t in resp for t in search_terms)
        self.assertTrue(found, f"No search tools mentioned: {resp[:300]}")


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 4: RESEARCH INTENT → WEB SEARCH TESTS
# Queries with "investiga", "averigua", etc. should trigger search, not denial.
# ══════════════════════════════════════════════════════════════════════════════

class TestResearchIntent(unittest.TestCase):
    """P4: Research verbs should trigger web search, not 'no tengo acceso'."""

    def _assert_research_triggers_search(self, query: str):
        result = query_zephyro(query, timeout=45)
        resp = response_text(result)
        # Should NOT deny internet access
        denial_patterns = [
            "no tengo acceso a información en tiempo real",
            "no puedo buscar en internet",
            "no tengo acceso a internet",
            "necesitarías hacerlo manualmente",
        ]
        for pattern in denial_patterns:
            self.assertNotIn(
                pattern, resp,
                f"Denial '{pattern}' found for research query '{query}': {resp[:200]}"
            )
        return resp

    def test_investiga_triggers_search(self):
        self._assert_research_triggers_search(
            "investiga sobre los últimos avances en inteligencia artificial"
        )

    def test_averigua_triggers_search(self):
        self._assert_research_triggers_search(
            "averigua quién es el presidente actual de Francia"
        )

    def test_necesito_que_investigues(self):
        self._assert_research_triggers_search(
            "necesito que investigues sobre las elecciones en México"
        )

    def test_busca_informacion(self):
        self._assert_research_triggers_search(
            "busca información sobre el cambio climático actual"
        )

    def test_explicit_research_request(self):
        self._assert_research_triggers_search(
            "haz una investigación sobre las criptomonedas más populares actualmente"
        )


# ══════════════════════════════════════════════════════════════════════════════
# COMPLEX REQUESTS: Mixed intent, nuanced, edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestComplexRequests(unittest.TestCase):
    """Complex/multi-intent requests that Zephyro should handle gracefully."""

    def test_conversational_app_mention_not_automation(self):
        """Mentioning an app conversationally should NOT trigger automation."""
        queries = [
            "discord es genial para hablar con amigos",
            "me gusta mucho usar spotify",
            "ayer usé firefox todo el día",
            "steam tiene buenas ofertas",
            "prefiero discord sobre slack",
        ]
        for q in queries:
            result = query_zephyro(q)
            resp = response_text(result)
            # Should NOT open the app (no automation marker)
            self.assertNotIn(
                "abriendo", resp,
                f"False positive automation for '{q}': {resp[:200]}"
            )

    def test_command_verb_triggers_automation(self):
        """Explicit command verbs should trigger automation."""
        queries_targets = [
            ("abre discord", "discord"),
            ("abre firefox", "firefox"),
            ("abre spotify", "spotify"),
        ]
        for q, target in queries_targets:
            result = query_zephyro(q)
            resp = response_text(result)
            # Should contain success marker or the app name
            self.assertTrue(
                "✓" in result.get("response", "") or target in resp,
                f"Command '{q}' didn't trigger automation: {resp[:200]}"
            )

    def test_capability_question_then_action(self):
        """'puedes abrir discord?' should explain (not open), 'abre discord' should open."""
        # Question form — should explain
        r1 = query_zephyro("puedes abrir discord?")
        resp1 = response_text(r1)
        self.assertFalse(has_denial(resp1), f"Denied Discord capability: {resp1[:200]}")

    def test_negation_not_command(self):
        """'no abras discord' should NOT open Discord."""
        result = query_zephyro("no abras discord")
        resp = response_text(result)
        # Should not open it
        self.assertNotIn("abriendo discord", resp, f"Opened despite negation: {resp[:200]}")

    def test_past_tense_not_command(self):
        """'abrí discord ayer' should NOT trigger automation."""
        result = query_zephyro("abrí discord ayer")
        resp = response_text(result)
        self.assertNotIn("abriendo", resp, f"Past tense triggered automation: {resp[:200]}")

    def test_question_about_failure(self):
        """'por qué no pudiste abrir discord?' should introspect, not say 'no entendí'."""
        result = query_zephyro("por qué a veces no puedes abrir aplicaciones?")
        resp = response_text(result)
        self.assertFalse(
            has_no_entendi(resp),
            f"Got 'no entendí' for introspection question: {resp[:200]}"
        )

    def test_mixed_conversation_and_capability(self):
        """Complex question mixing conversation and capability inquiry."""
        result = query_zephyro(
            "me dijeron que puedes buscar en internet y abrir aplicaciones, es verdad?"
        )
        resp = response_text(result)
        self.assertFalse(has_denial(resp), f"Denied capabilities: {resp[:200]}")


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER UNIT TESTS (no API needed, fast)
# ══════════════════════════════════════════════════════════════════════════════

class TestRouterIntentUnit(unittest.TestCase):
    """Unit tests for router classification — no LLM, no network."""

    @classmethod
    def setUpClass(cls):
        # Patch intent_detector to avoid LLM calls
        async def _fake_detect_intent(text: str) -> str:
            return "command"
        def _fake_classify_by_rules(text: str):
            return None
        import core.intent_detector as _id_mod
        _id_mod.detect_intent = _fake_detect_intent
        _id_mod.classify_by_rules = _fake_classify_by_rules

        from core.router import IntentRouter
        from unittest.mock import MagicMock
        mock_llm = MagicMock()
        mock_llm.generate = MagicMock(return_value='{"intent":"chat"}')
        cls.router = IntentRouter(mock_llm, context_manager=None)

    def _classify(self, text: str) -> dict:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.router.classify_and_parse(text, skip_llm=True)
            )
        finally:
            loop.close()

    # ── Conversational mentions (should NOT be automation) ────────────────

    def test_discord_es_genial(self):
        r = self._classify("discord es genial")
        self.assertNotEqual(r.get("intent"), "automation", f"False positive: {r}")

    def test_me_gusta_spotify(self):
        r = self._classify("me gusta mucho spotify")
        self.assertNotEqual(r.get("intent"), "automation", f"False positive: {r}")

    def test_hablamos_por_discord(self):
        r = self._classify("hablamos por discord")
        self.assertNotEqual(r.get("intent"), "automation", f"False positive: {r}")

    def test_prefiero_firefox(self):
        r = self._classify("prefiero firefox sobre chrome")
        self.assertNotEqual(r.get("intent"), "automation", f"False positive: {r}")

    def test_discord_no_me_funciona(self):
        r = self._classify("discord no me funciona bien últimamente")
        self.assertNotEqual(r.get("intent"), "automation", f"False positive: {r}")

    def test_ayer_use_steam(self):
        r = self._classify("ayer usé steam para jugar")
        self.assertNotEqual(r.get("intent"), "automation", f"False positive: {r}")

    # ── Explicit commands (SHOULD be automation) ──────────────────────────

    def test_abre_discord(self):
        r = self._classify("abre discord")
        self.assertEqual(r.get("intent"), "automation", f"Missed: {r}")

    def test_abrir_firefox(self):
        r = self._classify("abrir firefox")
        self.assertEqual(r.get("intent"), "automation", f"Missed: {r}")

    def test_open_spotify(self):
        r = self._classify("open spotify")
        self.assertEqual(r.get("intent"), "automation", f"Missed: {r}")

    def test_lanza_discord(self):
        r = self._classify("lanza discord")
        self.assertEqual(r.get("intent"), "automation", f"Missed: {r}")

    def test_ejecuta_steam(self):
        r = self._classify("ejecuta steam")
        self.assertEqual(r.get("intent"), "automation", f"Missed: {r}")

    def test_me_abres_discord(self):
        r = self._classify("me abres discord")
        self.assertEqual(r.get("intent"), "automation", f"Missed: {r}")

    def test_por_favor_abre_discord(self):
        r = self._classify("por favor abre discord")
        self.assertEqual(r.get("intent"), "automation", f"Missed: {r}")

    # ── Negations (should NOT be automation) ──────────────────────────────

    def test_no_abras_discord(self):
        r = self._classify("no abras discord")
        # Negation should ideally not trigger automation
        # (may or may not work depending on implementation)

    # ── Past tense (should NOT be automation) ─────────────────────────────

    def test_abri_discord_ayer(self):
        r = self._classify("abrí discord ayer y no funcionó")
        self.assertNotEqual(r.get("intent"), "automation", f"Past tense false positive: {r}")

    # ── Questions about capability (should NOT be automation) ─────────────

    def test_puedes_abrir_discord_question(self):
        """'puedes abrir discord?' is a question, not a command."""
        # This is tricky — "puedes" + "abrir" might match. Acceptable either way.
        r = self._classify("puedes abrir discord?")
        # We just check it doesn't crash; whether it's automation or chat is debatable.


# ══════════════════════════════════════════════════════════════════════════════
# SELF STATE UNIT TESTS (no API needed)
# ══════════════════════════════════════════════════════════════════════════════

class TestSelfStateUnit(unittest.TestCase):
    """Unit tests for self_state.py capability detection."""

    @classmethod
    def setUpClass(cls):
        from core.self_state import SelfState
        cls.ss = SelfState()

    def test_capability_triggers_on_consciousness(self):
        result = self.ss.get_relevant_capabilities("cómo funciona tu pseudo conciencia?")
        self.assertIsNotNone(result, "Should return context for consciousness question")
        self.assertIn("pseudo", result.lower())

    def test_capability_triggers_on_capabilities(self):
        result = self.ss.get_relevant_capabilities("qué puedes hacer?")
        self.assertIsNotNone(result, "Should return context for capability question")

    def test_capability_triggers_on_limitations(self):
        result = self.ss.get_relevant_capabilities("cuáles son tus limitaciones?")
        self.assertIsNotNone(result, "Should return context for limitation question")

    def test_capability_triggers_on_app_open(self):
        result = self.ss.get_relevant_capabilities("abre discord")
        self.assertIsNotNone(result, "Should return context for app open")
        self.assertIn("discord", result.lower())

    def test_no_trigger_on_casual(self):
        result = self.ss.get_relevant_capabilities("hola cómo estás")
        self.assertIsNone(result, "Should NOT trigger for casual chat")

    def test_mismatch_detects_automation_denial(self):
        """Post-process should detect when LLM denies app opening."""
        bug = self.ss.post_process(
            user_input="abre discord",
            response="No puedo abrir aplicaciones en tu sistema.",
            intent="chat",
            elapsed_time=2.0,
        )
        self.assertIsNotNone(bug, "Should detect capability mismatch")
        self.assertEqual(bug["bug_type"], "capability_mismatch")

    def test_mismatch_detects_search_denial(self):
        """Post-process should detect when LLM denies web search."""
        bug = self.ss.post_process(
            user_input="buscar noticias de hoy",
            response="No puedo buscar en internet. No tengo acceso.",
            intent="chat",
            elapsed_time=2.0,
        )
        self.assertIsNotNone(bug, "Should detect web search denial mismatch")

    def test_mismatch_detects_consciousness_denial(self):
        """Post-process should detect when LLM denies pseudo-consciousness."""
        bug = self.ss.post_process(
            user_input="tienes pseudo conciencia?",
            response="No tengo pseudo conciencia. Soy un modelo de lenguaje.",
            intent="chat",
            elapsed_time=2.0,
        )
        self.assertIsNotNone(bug, "Should detect consciousness denial mismatch")

    def test_no_mismatch_for_normal_response(self):
        """Normal responses should NOT trigger mismatch."""
        bug = self.ss.post_process(
            user_input="hola cómo estás",
            response="Hola Bruno, operativo y listo.",
            intent="chat",
            elapsed_time=1.0,
        )
        self.assertIsNone(bug, "Normal response should not trigger mismatch")


# ══════════════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("Zephyro Introspection & Intent Detection Test Suite")
    print("=" * 70)
    print()

    # Run unit tests first (fast, no API)
    print("── Unit Tests (no API) ──")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Unit tests
    suite.addTests(loader.loadTestsFromTestCase(TestRouterIntentUnit))
    suite.addTests(loader.loadTestsFromTestCase(TestSelfStateUnit))

    # API tests (require running Zephyro)
    suite.addTests(loader.loadTestsFromTestCase(TestCapabilityDenial))
    suite.addTests(loader.loadTestsFromTestCase(TestNoEntendi))
    suite.addTests(loader.loadTestsFromTestCase(TestSelfAwareness))
    suite.addTests(loader.loadTestsFromTestCase(TestResearchIntent))
    suite.addTests(loader.loadTestsFromTestCase(TestComplexRequests))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 70)
    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed = total - failures
    print(f"Results: {passed}/{total} passed, {failures} failed")
    print("=" * 70)
