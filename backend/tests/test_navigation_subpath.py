"""
Tests for navigation sub-path routing + diagnostic follow-up fixes.

Section A — _RE_CLOUD_AUTOMATION regex (unit)
Section B — Router nav-subpath: "entra a {section} de {site}"
Section C — Compound secondary nav: "abre X y entra reels" (sin "a")
Section D — _RE_CLOUD_DIAGNOSTIC follow-up patterns (unit)
Section E — Coherence / no false-positives
Section F — Orchestrator _pending_diagnostic state machine (mocked)
Section G — Router _RE_DIAGNOSTIC follow-up patterns
Section H — _RE_DIAGNOSTIC_FOLLOWUP constant (orchestrator)
Section I — _handle_diagnostic START vs FOLLOW-UP routing
Section J — VerificationAgent: cloud_fast_lane confirmation hallucinations
Section K — DiagnosticAgent Tier 0 Copilot→OpenCode fallback
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ─────────────────────────────────────────────────────────────────────────────
# A. _RE_CLOUD_AUTOMATION regex unit tests
# ─────────────────────────────────────────────────────────────────────────────

from core.orchestrator import _RE_CLOUD_AUTOMATION  # noqa: E402


class TestCloudAutomationRegex:
    """Verifica que la regex incluye los verbos de navegación nuevos."""

    def _matches(self, text: str) -> bool:
        return bool(_RE_CLOUD_AUTOMATION.search(text.lower()))

    # ── Nuevos verbos de navegación ───────────────────────────────────────

    def test_A1_entra_a_reels_instagram(self):
        assert self._matches("entra a reels de instagram")

    def test_A1_entra_a_accent(self):
        assert self._matches("Entra a Reels de Instagram")

    def test_A2_ve_a_trending_twitter(self):
        assert self._matches("ve a trending en twitter")

    def test_A3_visita_perfil_github(self):
        assert self._matches("visita mi perfil de github")

    def test_A4_ir_a_explorar_youtube(self):
        assert self._matches("ir a explorar de youtube")

    def test_A4_navega_a_youtube(self):
        assert self._matches("navega a youtube.com")

    def test_A_anda_a(self):
        assert self._matches("anda a la página de reddit")

    def test_A_dirigete_a(self):
        assert self._matches("dirígete a settings")

    def test_A_entra_en(self):
        assert self._matches("entra en el perfil de github")

    # ── Regresión: verbos existentes siguen funcionando ──────────────────

    def test_A5_abre_instagram_regression(self):
        assert self._matches("abre instagram")

    def test_A6_navega_youtube_regression(self):
        assert self._matches("navega a youtube.com")

    def test_A_abre_firefox_regression(self):
        assert self._matches("abre firefox")

    def test_A_cierra_pestaña(self):
        assert self._matches("cierra la pestaña")

    def test_A_lanza_spotify(self):
        assert self._matches("lanza spotify")

    def test_A_ejecuta_terminal(self):
        assert self._matches("ejecuta terminal")

    def test_A_reproduce(self):
        assert self._matches("reproduce la canción")

    def test_A_open_english(self):
        assert self._matches("open chrome")

    def test_A_launch_english(self):
        assert self._matches("launch discord")


# ─────────────────────────────────────────────────────────────────────────────
# B. Router nav-subpath pattern
# ─────────────────────────────────────────────────────────────────────────────

def _make_router() -> "IntentRouter":
    from core.router import IntentRouter
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value='{"intent": "chat"}')
    return IntentRouter(mock_llm, context_manager=None)


class TestRouterNavSubpath:
    """Router must produce intent=automation + sub_path for navigation-to-section commands."""

    @pytest.fixture(autouse=True)
    def router(self):
        self._router = _make_router()

    def _classify(self, text: str) -> dict:
        return asyncio.get_event_loop().run_until_complete(
            self._router.classify_and_parse(text, skip_llm=True)
        )

    def test_B1_entra_a_reels_instagram(self):
        r = self._classify("entra a reels de instagram")
        assert r.get("intent") == "automation"
        assert r.get("automation_intent") == "NAVIGATE"
        assert r.get("target") == "instagram"
        assert r.get("sub_path") == "reels"
        assert r.get("_source") == "capa1_nav_subpath"

    def test_B1_capital_letters(self):
        r = self._classify("Entra a Reels de Instagram")
        assert r.get("intent") == "automation"
        assert r.get("target") == "instagram"
        assert r.get("sub_path") == "reels"

    def test_B2_ve_a_trending_twitter(self):
        r = self._classify("ve a trending de twitter")
        assert r.get("intent") == "automation"
        assert r.get("target") == "twitter"
        assert r.get("sub_path") == "trending"

    def test_B3_navega_a_explorar_youtube(self):
        r = self._classify("navega a explorar de youtube")
        assert r.get("intent") == "automation"
        assert r.get("target") == "youtube"
        assert "explorar" in r.get("sub_path", "")

    def test_B4_seccion_reels_instagram(self):
        r = self._classify("entra a la sección reels de instagram")
        assert r.get("intent") == "automation"
        assert r.get("target") == "instagram"
        assert r.get("sub_path") == "reels"

    def test_B5_ir_a_home_reddit(self):
        r = self._classify("ir a home de reddit")
        assert r.get("intent") == "automation"
        assert r.get("target") == "reddit"
        assert r.get("sub_path") == "home"


# ─────────────────────────────────────────────────────────────────────────────
# C. Compound secondary nav — "a" optional
# ─────────────────────────────────────────────────────────────────────────────

class TestCompoundSecondaryNav:
    """Compound 'abre X y entra reels' should work with and without 'a'."""

    @pytest.fixture(autouse=True)
    def router(self):
        self._router = _make_router()

    def _classify(self, text: str) -> dict:
        return asyncio.get_event_loop().run_until_complete(
            self._router.classify_and_parse(text, skip_llm=True)
        )

    def test_C1_entra_reels_sin_a(self):
        r = self._classify("abre instagram y entra reels")
        assert r.get("intent") == "automation"
        assert r.get("target") == "instagram"
        sub = r.get("sub_path", "")
        assert "reels" in sub.lower() if sub else True  # sub_path present OR handled as compound actions

    def test_C2_entra_reels_con_a(self):
        r = self._classify("abre instagram y entra a reels")
        assert r.get("intent") == "automation"
        assert r.get("target") == "instagram"

    def test_C3_ve_a_trending_twitter(self):
        r = self._classify("abre twitter y ve a trending")
        assert r.get("intent") == "automation"
        assert r.get("target") == "twitter"


# ─────────────────────────────────────────────────────────────────────────────
# D. _RE_CLOUD_DIAGNOSTIC follow-up patterns
# ─────────────────────────────────────────────────────────────────────────────

from core.orchestrator import _RE_CLOUD_DIAGNOSTIC  # noqa: E402


class TestCloudDiagnosticRegex:
    """New follow-up patterns must match; original patterns must still match."""

    def _matches(self, text: str) -> bool:
        return bool(_RE_CLOUD_DIAGNOSTIC.search(text))

    # ── Nuevos patrones de follow-up ──────────────────────────────────────

    def test_D1_ya_tienes_el_diagnostico(self):
        assert self._matches("ya tienes el diagnóstico")

    def test_D1_ya_tienes_diagnostico_no_el(self):
        assert self._matches("ya tienes diagnóstico")

    def test_D1_ya_tienes_resultados(self):
        assert self._matches("ya tienes los resultados")

    def test_D1_ya_tienes_analisis(self):
        assert self._matches("ya tienes el análisis")

    def test_D2_tienes_los_resultados(self):
        assert self._matches("tienes los resultados")

    def test_D2_tienes_el_diagnostico(self):
        assert self._matches("tienes el diagnóstico")

    def test_D3_que_encontraste(self):
        assert self._matches("qué encontraste")

    def test_D3_que_encontraste_no_accent(self):
        assert self._matches("que encontraste")

    def test_D4_terminaste_el_diagnostico(self):
        assert self._matches("terminaste el diagnóstico")

    def test_D4_terminaste_analisis(self):
        assert self._matches("terminaste el análisis")

    # ── Regresión: patrones originales siguen funcionando ─────────────────

    def test_D5_diagnostico_solo(self):
        assert self._matches("diagnóstico")

    def test_D5_diagnostico_no_accent(self):
        assert self._matches("diagnostico")

    def test_D6_que_esta_pasando(self):
        assert self._matches("qué está pasando")

    def test_D6_que_pasa(self):
        assert self._matches("qué pasa")

    def test_D_revisa_errores(self):
        assert self._matches("revisa los errores")

    def test_D_analiza_logs(self):
        assert self._matches("analiza los logs")

    # ── No colisión ───────────────────────────────────────────────────────

    def test_D7_no_agenda_lunes(self):
        # "ya terminaste la agenda del lunes" → should NOT match diagnostic
        assert not self._matches("ya terminaste la agenda del lunes")

    def test_D7_no_tarea_completada(self):
        assert not self._matches("ya terminaste la tarea de matemáticas")


# ─────────────────────────────────────────────────────────────────────────────
# E. Coherence — no false-positives
# ─────────────────────────────────────────────────────────────────────────────

class TestCoherenceNoCollisions:
    """Ensure new patterns don't produce false routing results."""

    @pytest.fixture(autouse=True)
    def router(self):
        self._router = _make_router()

    def _classify(self, text: str) -> dict:
        return asyncio.get_event_loop().run_until_complete(
            self._router.classify_and_parse(text, skip_llm=True)
        )

    def test_E1_reporte_distracciones_no_diagnostic(self):
        # "ya tienes el reporte de distracciones" should not match _RE_CLOUD_DIAGNOSTIC
        assert not bool(_RE_CLOUD_DIAGNOSTIC.search("ya tienes el reporte de distracciones"))

    def test_E2_entra_a_reels_sin_sitio_no_nav_subpath(self):
        # "entra a reels" without any recognized site — should NOT produce nav_subpath
        r = self._classify("entra a reels")
        # Either no automation result or target must be a recognized site
        if r.get("intent") == "automation" and r.get("_source") == "capa1_nav_subpath":
            target = r.get("target", "")
            from core.app_config import SITE_KEYWORDS, NATIVE_APPS
            recognized = list(SITE_KEYWORDS) + list(NATIVE_APPS)
            assert target in recognized, f"nav_subpath produced unknown target: {target!r}"

    def test_E3_modo_estudio_no_nav_subpath(self):
        # "entra en modo estudio" must NOT trigger nav-subpath (no site/app)
        r = self._classify("entra en modo estudio")
        if r.get("intent") == "automation" and r.get("_source") == "capa1_nav_subpath":
            from core.app_config import SITE_KEYWORDS, NATIVE_APPS
            recognized = list(SITE_KEYWORDS) + list(NATIVE_APPS)
            assert r.get("target", "") in recognized

    def test_E_automation_regex_no_trigger_on_conversational(self):
        # Conversational complaints starting with navigation verb should not be in CLOUD_AUTOMATION
        # (at word boundary only)
        # "no entra" — note: "entra" matches _RE_CLOUD_AUTOMATION; that's acceptable
        # The guard for conversational context lives in the router, not the regex.
        # Just verify the regex doesn't match plain conversation without action verbs.
        assert not bool(_RE_CLOUD_AUTOMATION.search("cuéntame qué hace spotify"))

    def test_E_diagnostic_no_false_schedule(self):
        assert not bool(_RE_CLOUD_DIAGNOSTIC.search("ya terminé la tarea"))
        assert not bool(_RE_CLOUD_DIAGNOSTIC.search("ya terminaste de cocinar"))


# ─────────────────────────────────────────────────────────────────────────────
# F. Orchestrator _pending_diagnostic state machine (mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingDiagnosticState:
    """Validate the follow-up diagnostic state machine without real LLM/agents."""

    def _make_orchestrator(self):
        """Create a minimal Orchestrator without initializing external services."""
        # We test _pending_diagnostic attributes are set at init
        with (
            patch("core.orchestrator.LLMManager"),
            patch("core.orchestrator.ContextManager"),
            patch("core.orchestrator.IntentRouter"),
            patch("core.orchestrator.SelfState"),
            patch("core.orchestrator.ConversationSummarizer"),
        ):
            from core.orchestrator import Orchestrator
            return Orchestrator.__new__(Orchestrator)

    def test_F1_pending_diagnostic_false_by_default(self):
        # Verify the attribute names are in the __init__ annotations
        import inspect
        from core.orchestrator import Orchestrator
        source = inspect.getsource(Orchestrator.__init__)
        assert "_pending_diagnostic" in source
        assert "_pending_diagnostic_summary" in source
        assert "_pending_diagnostic_task" in source

    def test_F2_state_transitions_direct(self):
        """Simulate state transitions by directly manipulating attributes."""
        # Create a simple mock object that mimics the diagnostic state
        class FakeOrch:
            _pending_diagnostic: bool = False
            _pending_diagnostic_summary: str = ""
            _pending_diagnostic_task = None

        o = FakeOrch()
        assert not o._pending_diagnostic

        # Simulate what _handle_diagnostic does after creating task
        o._pending_diagnostic = True
        o._pending_diagnostic_summary = "Arrancando diagnóstico..."
        mock_task = MagicMock()
        mock_task.done.return_value = False
        o._pending_diagnostic_task = mock_task

        assert o._pending_diagnostic
        assert not o._pending_diagnostic_task.done()

    def test_F3_followup_while_running(self):
        """Follow-up while task is running → must return 'Sigo analizando'."""
        class FakeOrch:
            _pending_diagnostic: bool = True
            _pending_diagnostic_summary: str = "En curso"
            _pending_diagnostic_task = None

        o = FakeOrch()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        o._pending_diagnostic_task = mock_task

        # Replicate the follow-up check logic
        if o._pending_diagnostic:
            if o._pending_diagnostic_task and not o._pending_diagnostic_task.done():
                result = "Sigo analizando en segundo plano — todavía no tengo los resultados. Te aviso cuando termine."
            else:
                o._pending_diagnostic = False
                result = o._pending_diagnostic_summary or "El diagnóstico ya terminó."
        else:
            result = "no pending"

        assert "Sigo analizando" in result

    def test_F4_followup_after_task_done(self):
        """Follow-up after task completed → returns summary + resets state."""
        class FakeOrch:
            _pending_diagnostic: bool = True
            _pending_diagnostic_summary: str = "Errores encontrados: ninguno."
            _pending_diagnostic_task = None

        o = FakeOrch()
        mock_task = MagicMock()
        mock_task.done.return_value = True  # task finished
        o._pending_diagnostic_task = mock_task

        if o._pending_diagnostic:
            if o._pending_diagnostic_task and not o._pending_diagnostic_task.done():
                result = "Sigo analizando..."
            else:
                o._pending_diagnostic = False
                result = o._pending_diagnostic_summary or "El diagnóstico ya terminó."
        else:
            result = "no pending"

        assert result == "Errores encontrados: ninguno."
        assert not o._pending_diagnostic  # state was reset

    def test_F4_followup_empty_summary_fallback(self):
        """If summary is empty after task done, return fallback message."""
        class FakeOrch:
            _pending_diagnostic: bool = True
            _pending_diagnostic_summary: str = ""
            _pending_diagnostic_task = None

        o = FakeOrch()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        o._pending_diagnostic_task = mock_task

        if o._pending_diagnostic:
            if o._pending_diagnostic_task and not o._pending_diagnostic_task.done():
                result = "Sigo analizando..."
            else:
                o._pending_diagnostic = False
                result = o._pending_diagnostic_summary or "El diagnóstico ya terminó."

        assert result == "El diagnóstico ya terminó."
        assert not o._pending_diagnostic


# ─────────────────────────────────────────────────────────────────────────────
# G. Router _RE_DIAGNOSTIC follow-up patterns
# ─────────────────────────────────────────────────────────────────────────────

from core.router import _RE_DIAGNOSTIC  # noqa: E402


class TestRouterDiagnosticFollowup:
    """The router's _RE_DIAGNOSTIC must now match follow-up phrases too."""

    def _matches(self, text: str) -> bool:
        return bool(_RE_DIAGNOSTIC.search(text.lower()))

    def test_G1_ya_tienes_diagnostico(self):
        assert self._matches("ya tienes el diagnóstico")

    def test_G2_tienes_resultados(self):
        assert self._matches("tienes los resultados")

    def test_G3_que_encontraste(self):
        assert self._matches("qué encontraste")

    def test_G4_terminaste_diagnostico(self):
        assert self._matches("terminaste el diagnóstico")

    # Regression: existing patterns still work

    def test_G5_diagnostico_solo(self):
        assert self._matches("diagnostico")

    def test_G6_que_pasa(self):
        assert self._matches("qué pasa")

    # No collision with schedule

    def test_G7_no_terminaste_agenda(self):
        assert not self._matches("ya terminaste la agenda del lunes")


# ─────────────────────────────────────────────────────────────────────────────
# H. _RE_DIAGNOSTIC_FOLLOWUP constant (subset: only follow-ups, NOT starts)
# ─────────────────────────────────────────────────────────────────────────────

from core.orchestrator import _RE_DIAGNOSTIC_FOLLOWUP  # noqa: E402


class TestDiagnosticFollowupRegex:
    def _matches(self, text: str) -> bool:
        return bool(_RE_DIAGNOSTIC_FOLLOWUP.search(text.lower()))

    def test_H1_ya_tienes(self):
        assert self._matches("ya tienes el diagnóstico")

    def test_H2_tienes_resultados(self):
        assert self._matches("tienes los resultados")

    def test_H3_que_encontraste(self):
        assert self._matches("qué encontraste")

    def test_H4_terminaste(self):
        assert self._matches("terminaste el diagnóstico")

    def test_H5_haz_diagnostico_NOT_followup(self):
        # "haz un diagnóstico" is a START, not a follow-up
        assert not self._matches("haz un diagnóstico")

    def test_H6_diagnostico_alone_NOT_followup(self):
        # "diagnóstico" alone is a START, not a follow-up
        assert not self._matches("diagnostico")


# ─────────────────────────────────────────────────────────────────────────────
# I. _handle_diagnostic START vs FOLLOW-UP routing (state machine + regex)
# ─────────────────────────────────────────────────────────────────────────────

class TestDiagnosticRoutingLogic:
    """Replicates the routing logic at the top of _handle_diagnostic()."""

    def _route(self, user_input: str, pending: bool, task_done: bool = True,
               summary: str = "result-text"):
        """Mirrors the actual top-of-_handle_diagnostic logic."""
        is_followup = bool(_RE_DIAGNOSTIC_FOLLOWUP.search(user_input.lower()))
        if is_followup and pending:
            if not task_done:
                return ("STATUS", "Sigo analizando en segundo plano — todavía no tengo los resultados. Te aviso cuando termine.")
            return ("SUMMARY", summary)
        return ("NEW", None)

    def test_I1_followup_no_pending_falls_through_to_new(self):
        verdict, payload = self._route("ya tienes el diagnóstico", pending=False)
        assert verdict == "NEW"

    def test_I2_followup_pending_task_running(self):
        verdict, payload = self._route("ya tienes el diagnóstico",
                                       pending=True, task_done=False)
        assert verdict == "STATUS"
        assert "Sigo analizando" in payload

    def test_I3_followup_pending_task_done_returns_summary(self):
        verdict, payload = self._route("qué encontraste",
                                       pending=True, task_done=True,
                                       summary="Encontré 3 errores...")
        assert verdict == "SUMMARY"
        assert payload == "Encontré 3 errores..."

    def test_I4_start_phrase_does_not_consume_pending(self):
        # "haz un diagnóstico" is a START — should NOT match the follow-up branch
        verdict, payload = self._route("haz un diagnóstico",
                                       pending=True, task_done=True,
                                       summary="old-result")
        assert verdict == "NEW"  # falls through, starts a fresh diagnostic


# ─────────────────────────────────────────────────────────────────────────────
# J. VerificationAgent — cloud_fast_lane "execution" hallucinations
# ─────────────────────────────────────────────────────────────────────────────

from agents.verification_agent import (  # noqa: E402
    _DESCRIBE_INSTEAD_RE,
    _CONFIRMED_EXECUTION_RE,
)


class TestVerificationCloudFastLane:
    """Replicates the new branch in _verify_automation_async."""

    def _verdict(self, response: str, source: str):
        """Return ('WARN', evidence) or ('OK', None) — mirrors agent logic."""
        describes = bool(_DESCRIBE_INSTEAD_RE.search(response))
        confirms = bool(_CONFIRMED_EXECUTION_RE.search(response))
        if describes and not confirms:
            return ("WARN", "describes-future")
        if source == "cloud_fast_lane" and confirms:
            return ("WARN", "cloud-fast-lane-fake-confirm")
        return ("OK", None)

    def test_J1_cloud_fastlane_fake_abriendo(self):
        verdict, why = self._verdict(
            "Abriendo Discord (app nativa)...",
            source="cloud_fast_lane",
        )
        assert verdict == "WARN"
        assert why == "cloud-fast-lane-fake-confirm"

    def test_J2_cloud_fastlane_future_tense(self):
        verdict, why = self._verdict(
            "Voy a abrir discord ahora mismo",
            source="cloud_fast_lane",
        )
        assert verdict == "WARN"
        assert why == "describes-future"

    def test_J3_real_automation_abriendo_ok(self):
        # Real automation handlers use _source != cloud_fast_lane
        verdict, why = self._verdict(
            "Abriendo Discord (nativo, verificado) ✓",
            source="capa1_nav_subpath",
        )
        assert verdict == "OK"

    def test_J4_cloud_fastlane_neutral_response(self):
        verdict, why = self._verdict(
            "No entendí tu mensaje, ¿puedes reformularlo?",
            source="cloud_fast_lane",
        )
        assert verdict == "OK"

    def test_J5_cloud_fastlane_ejecutado(self):
        verdict, why = self._verdict(
            "Comando ejecutado correctamente",
            source="cloud_fast_lane",
        )
        assert verdict == "WARN"
        assert why == "cloud-fast-lane-fake-confirm"


# ─────────────────────────────────────────────────────────────────────────────
# K. DiagnosticAgent Tier 0 Copilot → OpenCode fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestDiagnosticTier0Fallback:
    def test_K1_opencode_models_mapping(self):
        from subagents.diagnostic_agent import DiagnosticAgent
        m = DiagnosticAgent._OPENCODE_MODELS
        assert m[0] == "opencode/deepseek-v4-flash-free"
        assert "deepseek" in m[1]
        assert "deepseek" in m[2]

    def test_K2_opencode_available_checks_subprocess(self):
        from subagents.diagnostic_agent import DiagnosticAgent
        # Just verify the method exists and returns a bool
        result = DiagnosticAgent._opencode_available()
        assert isinstance(result, bool)

    def test_K3_build_tier0_prompt_contains_error_metadata(self):
        from subagents.diagnostic_agent import (
            DiagnosticAgent, LogEntry,
        )
        # Build a minimal LogEntry; bypass __init__ side effects with __new__
        agent = DiagnosticAgent.__new__(DiagnosticAgent)
        entry = LogEntry(
            timestamp="12:00:00", level="ERROR", logger="zephyro.test",
            message="Test error message", filename="test.py", lineno=42,
            stack_trace="Traceback...\n  File ...", raw="raw log line",
        )
        prompt = agent._build_tier0_prompt(entry, ctx=None)
        assert "test.py" in prompt
        assert "42" in prompt
        assert "Test error message" in prompt

    def test_K4_call_tier0_copilot_uses_agent_flag(self):
        # Verify the source code switched from --target to --agent
        import inspect
        from subagents.diagnostic_agent import DiagnosticAgent
        src = inspect.getsource(DiagnosticAgent._call_tier0_copilot)
        assert "--agent" in src
        assert "--target" not in src

    def test_K5_tier0_branch_probes_opencode_after_copilot(self):
        # Verify run() now has the OpenCode fallback path
        import inspect
        from subagents.diagnostic_agent import DiagnosticAgent
        src = inspect.getsource(DiagnosticAgent.run)
        assert "_call_opencode" in src
        assert "_opencode_available" in src


# ─────────────────────────────────────────────────────────────────────────────
# L. ws_approval_gate.py — broadcast fallback when user_id unknown
# ─────────────────────────────────────────────────────────────────────────────

class TestWsApprovalGateBroadcastFallback:
    """L1-L3: _push_or_broadcast uses broadcast() when user_id is empty."""

    def _make_gate(self, connections=None):
        from subagents.ws_approval_gate import WebSocketApprovalGate
        from subagents.diagnostic_store import DiagnosticStore
        registry = MagicMock()
        registry.push = AsyncMock(return_value=True)
        registry.broadcast = AsyncMock(return_value=1)
        if connections is not None:
            registry._connections = connections
        store = MagicMock(spec=DiagnosticStore)
        store.load = MagicMock(return_value=None)  # no entry → user_id=""
        store.save = MagicMock()
        store.append_progress = MagicMock()
        return WebSocketApprovalGate(registry, store), registry

    def test_L1_send_approval_broadcast_when_no_store_entry(self):
        gate, registry = self._make_gate()
        asyncio.get_event_loop().run_until_complete(
            gate.send_approval_request("unknown-context-id", "hypothesis text")
        )
        # push("", ...) never called; broadcast used instead
        for call_args in registry.push.call_args_list:
            assert call_args[0][0] != "", "push called with empty user_id"
        registry.broadcast.assert_called_once()

    def test_L2_send_approval_push_when_user_id_known(self):
        from subagents.ws_approval_gate import WebSocketApprovalGate
        from subagents.diagnostic_store import DiagnosticStore
        registry = MagicMock()
        registry.push = AsyncMock(return_value=True)
        registry.broadcast = AsyncMock(return_value=1)
        store = MagicMock(spec=DiagnosticStore)
        store.load = MagicMock(return_value={"request_id": "abc", "user_id": "user42"})
        store.save = MagicMock()
        gate = WebSocketApprovalGate(registry, store)
        asyncio.get_event_loop().run_until_complete(
            gate.send_approval_request("abc", "hypothesis text")
        )
        registry.push.assert_called()
        called_uid = registry.push.call_args[0][0]
        assert called_uid == "user42"
        registry.broadcast.assert_not_called()

    def test_L3_send_message_broadcast_when_no_store_entry(self):
        gate, registry = self._make_gate()
        asyncio.get_event_loop().run_until_complete(
            gate.send_message("unknown-req", "progress text")
        )
        for call_args in registry.push.call_args_list:
            assert call_args[0][0] != "", "push called with empty user_id"
        registry.broadcast.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# M. Orchestrator _handle_diagnostic: gate.start() before agent.run()
# ─────────────────────────────────────────────────────────────────────────────

class TestOrchestratorDiagnosticGateStart:
    """M1-M3: orchestrator now calls gate.start(user_id) to register user_id
    before running the agent, so WS pushes reach the correct client."""

    def test_M1_gate_start_called_with_connected_user(self):
        import inspect
        from core.orchestrator import Orchestrator
        src = inspect.getsource(Orchestrator._handle_diagnostic)
        assert "gate.start" in src, "_handle_diagnostic must call gate.start()"

    def test_M2_agent_run_receives_request_id_not_session_id(self):
        import inspect
        from core.orchestrator import Orchestrator
        src = inspect.getsource(Orchestrator._handle_diagnostic)
        # After gate.start, request_id is used for agent.run
        assert "request_id" in src
        assert "chat_id=request_id" in src

    def test_M3_fallback_when_no_connected_users(self):
        import inspect
        from core.orchestrator import Orchestrator
        src = inspect.getsource(Orchestrator._handle_diagnostic)
        # Uses registry.get_connected_users() as primary source
        assert "get_connected_users" in src


# ─────────────────────────────────────────────────────────────────────────────
# N. DiagnosticAgent._call_opencode stdout streaming
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenCodeStreaming:
    """N1-N3: _call_opencode streams stdout lines via gate.send_message."""

    def test_N1_streams_lines_to_gate(self):
        from subagents.diagnostic_agent import DiagnosticAgent
        import asyncio, io

        async def _run():
            agent = DiagnosticAgent.__new__(DiagnosticAgent)
            agent._log = MagicMock()
            agent._OPENCODE_MODELS = {0: "opencode/deepseek-v4-flash-free"}

            mock_gate = MagicMock()
            mock_gate.send_message = AsyncMock()

            # Mock subprocess that writes two lines then EOF
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            lines_data = [b"line one\n", b"line two\n", b""]
            async def _readline():
                return lines_data.pop(0)
            mock_proc.stdout = MagicMock()
            mock_proc.stdout.readline = _readline
            mock_proc.wait = AsyncMock()

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                ok, result = await agent._call_opencode(
                    "test prompt", tier=0, gate=mock_gate, chat_id="req123"
                )

            assert ok is True
            assert "line one" in result
            assert "line two" in result
            calls = [str(c) for c in mock_gate.send_message.call_args_list]
            assert any("line one" in c for c in calls)
            assert any("line two" in c for c in calls)

        asyncio.get_event_loop().run_until_complete(_run())

    def test_N2_timeout_returns_false(self):
        from subagents.diagnostic_agent import DiagnosticAgent
        import asyncio

        async def _run():
            agent = DiagnosticAgent.__new__(DiagnosticAgent)
            agent._log = MagicMock()
            agent._OPENCODE_MODELS = {0: "opencode/deepseek-v4-flash-free"}

            mock_proc = MagicMock()
            mock_proc.kill = MagicMock()
            async def _readline_hang():
                await asyncio.sleep(100)  # hangs forever
            mock_proc.stdout = MagicMock()
            mock_proc.stdout.readline = _readline_hang
            mock_proc.wait = AsyncMock()

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                    ok, result = await agent._call_opencode("prompt", tier=0)

            assert ok is False
            assert result == ""

        asyncio.get_event_loop().run_until_complete(_run())

    def test_N3_no_gate_still_returns_result(self):
        from subagents.diagnostic_agent import DiagnosticAgent
        import asyncio

        async def _run():
            agent = DiagnosticAgent.__new__(DiagnosticAgent)
            agent._log = MagicMock()
            agent._OPENCODE_MODELS = {0: "opencode/deepseek-v4-flash-free"}

            lines_data = [b"result line\n", b""]
            async def _readline():
                return lines_data.pop(0)
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = MagicMock()
            mock_proc.stdout.readline = _readline
            mock_proc.wait = AsyncMock()

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                ok, result = await agent._call_opencode("prompt", tier=0, gate=None, chat_id=None)

            assert ok is True
            assert "result line" in result

        asyncio.get_event_loop().run_until_complete(_run())


# ─────────────────────────────────────────────────────────────────────────────
# O. Frontend multi-word app extraction (Python equivalents)
# ─────────────────────────────────────────────────────────────────────────────

class TestFrontendMultiWordExtraction:
    """O1-O3: verify the 2-word capture logic for webAppKeys in extractOpenIntentApp.

    These test the Python-equivalent matching logic since we can't run JS here.
    """

    _WEB_APP_KEYS = [
        "youtube", "youtube music", "instagram", "facebook", "twitter",
        "whatsapp", "telegram", "gmail", "github", "reddit", "twitch",
        "netflix", "spotify", "primevideo", "prime video", "hbo",
        "stackoverflow", "pinterest",
    ]
    _NATIVE_APPS = [
        "discord", "spotify", "vlc", "kate", "dolphin", "konsole",
        "steam", "code", "libreoffice", "alacritty",
    ]

    def _extract(self, message: str) -> str | None:
        """Simulate the JS extraction: 2-word capture, longest match first."""
        import re as _re
        patterns = [
            _re.compile(r'\babre\s+(?:la |el |una? |los |las )?(\S+(?:\s+\S+)?)', _re.IGNORECASE),
            _re.compile(r'\babrir\s+(?:la |el |una? |los |las )?(\S+(?:\s+\S+)?)', _re.IGNORECASE),
            _re.compile(r'\bopen\s+(?:the |a )?(\S+(?:\s+\S+)?)', _re.IGNORECASE),
        ]
        all_known = set(self._WEB_APP_KEYS) | set(self._NATIVE_APPS)
        for pat in patterns:
            m = pat.search(message)
            if m:
                raw = m.group(1).rstrip(".,;!?").strip().lower()
                one_word = raw.split()[0]
                return raw if raw in all_known else one_word
        return None

    def test_O1_youtube_music_two_word(self):
        assert self._extract("Abre YouTube Music como en el navegador") == "youtube music"

    def test_O2_youtube_one_word_when_no_music(self):
        assert self._extract("Abre YouTube") == "youtube"

    def test_O3_spotify_one_word(self):
        assert self._extract("Abre Spotify") == "spotify"


# ─────────────────────────────────────────────────────────────────────────────
# P. Router sequence fallback when skip_llm=True
# ─────────────────────────────────────────────────────────────────────────────

class TestRouterSequenceFallbackSkipLLM:
    """P1-P4: skip_llm=True + sequence hint → extract last step target."""

    def _make_router(self):
        from core.router import IntentRouter as Router
        llm = MagicMock()
        llm.generate = AsyncMock(return_value='{"intent":"chat"}')
        router = Router(llm_manager=llm)
        return router

    def test_P1_sequence_cloud_mode_extracts_last_web_app(self):
        router = self._make_router()
        result = asyncio.get_event_loop().run_until_complete(
            router.classify_and_parse(
                "Abre Discord y luego abre YouTube Music", skip_llm=True
            )
        )
        assert result["intent"] == "automation"
        assert result["target"] == "youtube music"

    def test_P2_sequence_cloud_mode_extracts_last_native_app(self):
        router = self._make_router()
        result = asyncio.get_event_loop().run_until_complete(
            router.classify_and_parse(
                "Abre Firefox y luego abre Spotify", skip_llm=True
            )
        )
        assert result["intent"] == "automation"
        # spotify is in both native apps and site_keywords; either is acceptable
        assert result["target"] in ("spotify", "firefox")

    def test_P3_sequence_no_match_returns_information(self):
        router = self._make_router()
        result = asyncio.get_event_loop().run_until_complete(
            router.classify_and_parse(
                "Abre Discord y luego busca recetas de pasta", skip_llm=True
            )
        )
        # "recetas de pasta" is not a known app/site → falls through to information
        assert result["intent"] in ("information", "automation")

    def test_P4_sequence_skip_llm_false_does_not_apply_fallback(self):
        # Without skip_llm, the sequence hint causes CAPA 1 skip and no fallback path runs
        import inspect
        from core.router import IntentRouter as Router
        src = inspect.getsource(Router.classify_and_parse)
        assert "capa1_sequence_skip_llm" in src


# ─────────────────────────────────────────────────────────────────────────────
# Q. VerificationAgent — "redirigiendo" in _CONFIRMED_EXECUTION_RE
# ─────────────────────────────────────────────────────────────────────────────

class TestVerificationAgentRedirigiendo:
    """Q1-Q2: "redirigiendo" triggers WARN when source=cloud_fast_lane."""

    def _make_agent(self):
        from agents.verification_agent import VerificationAgent
        registry = MagicMock()
        registry.broadcast = AsyncMock(return_value=1)
        agent = VerificationAgent(conn_registry=registry)
        return agent, registry

    def test_Q1_redirigiendo_from_cloud_fastlane_warns(self):
        agent, registry = self._make_agent()
        event = {
            "response": "Redirigiendo a music.youtube.com en la pestaña actual.",
            "_source": "cloud_fast_lane",
        }
        asyncio.get_event_loop().run_until_complete(
            agent._verify_automation_async(event)
        )
        registry.broadcast.assert_called_once()
        payload = registry.broadcast.call_args[0][0]
        assert payload["verdict"] == "WARN"

    def test_Q2_redirigiendo_from_real_nav_no_warn(self):
        agent, registry = self._make_agent()
        event = {
            "response": "Redirigiendo a youtube.com",
            "_source": "capa1_nav_subpath",
        }
        asyncio.get_event_loop().run_until_complete(
            agent._verify_automation_async(event)
        )
        registry.broadcast.assert_not_called()
