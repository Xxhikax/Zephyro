#!/usr/bin/env python3
"""
Guardrail determinístico de automatizaciones.

Covers:
  - core.action_risk_rules.classify_action — READ / REVERSIBLE / HIGH por forma de analysis
  - Orchestrator pending automation — por sesión, TTL, sin cruce entre canales
  - Orchestrator.handle_input — un "sí" suelto no re-ejecuta una acción HIGH
  - Orchestrator._execute_candidate — el camino multi-tarea también pasa por el guard

All pure-Python + mocks. No browser, no network, no LLM (NO Edge — tests-safety.md).
Orchestrator methods are exercised via Orchestrator.__new__ (skips the heavy __init__).

Usage:
  backend/venv/Scripts/python.exe -m pytest backend/tests/test_action_guard.py -v
"""
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.action_risk_rules import ActionRisk, classify_action


def _interact(action, target, text=None):
    a = {"intent": "automation", "automation_intent": "INTERACT",
         "interact_action": action, "interact_target": target}
    if text is not None:
        a["interact_text"] = text
    return a


class TestClassifyAction(unittest.TestCase):

    def test_read_only_intents(self):
        for a in (
            {"intent": "automation", "automation_intent": "NAVIGATE", "target": "youtube"},
            {"intent": "automation", "automation_intent": "PLAY_MEDIA", "search_query": "lofi"},
            {"intent": "automation", "automation_intent": "SEARCH", "search_query": "pizza"},
            _interact("scroll", "down"),
            _interact("screenshot", ""),
        ):
            self.assertIs(classify_action(a).level, ActionRisk.READ, a)

    def test_playback_control_is_not_read(self):
        """PLAYBACK_CONTROL toca la página activa del usuario.

        `_set_playback` presiona 'k' sobre `self.page` y `stop_music` la
        navega a about:blank. No pide confirmación (sería insoportable),
        pero llamarlo READ era mentir en el registro de la acción.
        """
        for ctl in ("pause", "resume", "next", "prev", "mute", "close"):
            a = {"intent": "automation", "automation_intent": "PLAYBACK_CONTROL",
                 "control_action": ctl}
            v = classify_action(a)
            self.assertIs(v.level, ActionRisk.REVERSIBLE, ctl)
            self.assertFalse(v.needs_confirmation, ctl)

    def test_irreversible_clicks_are_high(self):
        for target in ("Comprar ahora", "Confirmar pago", "Enviar", "Submit",
                       "Eliminar", "Reservar", "Iniciar sesión", "Publicar"):
            v = classify_action(_interact("click", target))
            self.assertIs(v.level, ActionRisk.HIGH, target)
            self.assertTrue(v.needs_confirmation)

    def test_generic_click_is_reversible(self):
        v = classify_action(_interact("click", "Play"))
        self.assertIs(v.level, ActionRisk.REVERSIBLE)
        self.assertFalse(v.needs_confirmation)

    def test_type_into_search_box_is_reversible(self):
        v = classify_action(_interact("type", "input[name=q]", text="recetas de pizza"))
        self.assertIs(v.level, ActionRisk.REVERSIBLE)

    def test_type_into_password_field_is_high(self):
        v = classify_action(_interact("type", "input[type=password]", text="hunter2"))
        self.assertIs(v.level, ActionRisk.HIGH)

    def test_router_regex_shape_type_with_en(self):
        # Formato del router regex / planner: action + target "texto en selector"
        a = {"intent": "automation", "automation_intent": "INTERACT",
             "action": "type", "target": "1234 en campo cvv"}
        self.assertIs(classify_action(a).level, ActionRisk.HIGH)

    def test_legacy_search_intent_click(self):
        a = {"intent": "web_search", "action": "browser_click", "target": "Comprar"}
        self.assertIs(classify_action(a).level, ActionRisk.HIGH)

    def test_close_common_app_is_reversible(self):
        a = {"intent": "automation", "action": "close", "target": "spotify"}
        self.assertIs(classify_action(a).level, ActionRisk.REVERSIBLE)

    def test_close_protected_or_ambiguous_is_high(self):
        for target in ("explorer", "python", "powershell", "ed", "Ollama.exe"):
            a = {"intent": "automation", "action": "close", "target": target}
            self.assertIs(classify_action(a).level, ActionRisk.HIGH, target)

    def test_update_system_is_high(self):
        a = {"intent": "automation", "action": "update", "target": ""}
        self.assertIs(classify_action(a).level, ActionRisk.HIGH)

    def test_sequence_takes_max_risk(self):
        a = {"intent": "automation", "automation_intent": "SEQUENCE", "steps": [
            {"intent": "automation", "automation_intent": "NAVIGATE", "target": "mercadolibre"},
            _interact("click", "Comprar ahora"),
        ]}
        self.assertIs(classify_action(a).level, ActionRisk.HIGH)

    def test_multi_action_items_dispatch_as_automation(self):
        # orchestrator ejecuta cada item con automation_intent="OPEN_APP" → cae a close_app
        a = {"intent": "automation", "automation_intent": "MULTI_ACTION", "actions": [
            {"action": "open", "target": "spotify"},
            {"action": "close", "target": "explorer", "automation_intent": "OPEN_APP"},
        ]}
        self.assertIs(classify_action(a).level, ActionRisk.HIGH)

    def test_no_analysis_at_all_is_read(self):
        """Sin analysis no hay nada que ejecutar."""
        self.assertIs(classify_action(None).level, ActionRisk.READ)

    def test_unrecognized_shape_does_not_default_to_read(self):
        """El default dejó de ser READ: 'no sé qué es esto' no es 'solo lee'."""
        for a in ({}, {"intent": "automation"}, {"intent": "automation", "action": ""}):
            v = classify_action(a)
            self.assertIsNot(v.level, ActionRisk.READ, a)
            self.assertIs(v.level, ActionRisk.REVERSIBLE, a)

    def test_unknown_automation_intent_is_high(self):
        """Un intent fuera del universo conocido pide confirmación.

        Antes caía al fallthrough de READ: agregar un automation_intent
        nuevo lo hacía nacer sin guardrail.
        """
        a = {"intent": "automation", "automation_intent": "WIPE_EVERYTHING",
             "target": "x"}
        v = classify_action(a)
        self.assertIs(v.level, ActionRisk.HIGH)
        self.assertTrue(v.needs_confirmation)

    # ── Regresión: el desencuentro gate/ejecutor ────────────────────────
    # `close`/`update` vivían detrás de `if automation_intent not in
    # _DISPATCHED_INTENTS`. El router SIEMPRE emite un automation_intent, así
    # que `_classify_close` y `_PROTECTED_PROCESSES` eran inalcanzables desde
    # producción. Estos tests usan la forma REAL del router — con
    # automation_intent — que es justo lo que los tests de arriba omitían.
    def test_close_is_classified_with_real_router_shape(self):
        for ai in ("NAVIGATE", "INTERACT", "OPEN_APP", "SEARCH", "PLAY_MEDIA"):
            for target in ("explorer", "python", "powershell", "ollama"):
                a = {"intent": "automation", "action": "close",
                     "target": target, "automation_intent": ai}
                v = classify_action(a)
                self.assertIs(v.level, ActionRisk.HIGH, f"{ai} / {target}")
                self.assertTrue(v.needs_confirmation, f"{ai} / {target}")

    def test_update_is_classified_with_real_router_shape(self):
        a = {"intent": "automation", "action": "update", "target": "windows",
             "automation_intent": "NAVIGATE"}
        self.assertIs(classify_action(a).level, ActionRisk.HIGH)

    # ── Regresión: apps que sostienen trabajo sin guardar ───────────────
    def test_closing_an_app_with_unsaved_work_is_high(self):
        """`close_app` mata el proceso: no hay diálogo de recuperación."""
        for target in ("word", "excel", "winword", "Microsoft Word", "chrome",
                       "msedge", "firefox", "notepad", "obsidian", "photoshop",
                       "pycharm", "acrobat"):
            for ai in ("NAVIGATE", "OPEN_APP"):
                a = {"intent": "automation", "action": "close",
                     "target": target, "automation_intent": ai}
                v = classify_action(a)
                self.assertIs(v.level, ActionRisk.HIGH, f"{ai} / {target}")

    def test_closing_an_app_without_documents_stays_reversible(self):
        for target in ("spotify", "discord", "steam", "vlc", "calculator"):
            a = {"intent": "automation", "action": "close", "target": target,
                 "automation_intent": "NAVIGATE"}
            self.assertIs(classify_action(a).level, ActionRisk.REVERSIBLE, target)

    def test_protected_match_is_by_word_not_substring(self):
        """«password» no menciona a «word»; «monitoreo» no menciona a «monitor».

        El match por substring es lo que hace que un target genérico alcance
        más de lo que el usuario nombró.
        """
        a = {"intent": "automation", "action": "close", "target": "passwordmanager",
             "automation_intent": "NAVIGATE"}
        self.assertIs(classify_action(a).level, ActionRisk.REVERSIBLE)


# ═══════════════════════════════════════════════════════════════════════

from core.orchestrator import Orchestrator
from core.request_context import RequestContext

_HIGH = _interact("click", "Comprar ahora")
_SAFE = {"intent": "automation", "automation_intent": "NAVIGATE", "target": "youtube"}


def _bare_orch(last_automation=None):
    o = Orchestrator.__new__(Orchestrator)
    o._pending_automations = {}
    o._pending_clarification = None
    o._pending_extra_search_task = None
    o._call_turn_buffer = None
    o.session_id = "default"
    o.user_role = o.user_name = None
    o.authenticated = False
    o.console = MagicMock()
    o.context = MagicMock()
    o.context.reset_for_new_session.return_value = False
    o.context.get_last_automation.return_value = last_automation
    o.self_state = MagicMock()
    o.automation = MagicMock()
    o.automation.execute = AsyncMock(return_value="ejecutado")
    return o


def _ctx(sid):
    return RequestContext(user_role="admin", user_name="Bruno", authenticated=True, session_id=sid)


class TestPendingAutomationPerSession(unittest.IsolatedAsyncioTestCase):

    def test_pending_is_isolated_per_session(self):
        o = _bare_orch()
        o._set_pending_automation("desktop", _HIGH)
        self.assertIsNone(o._pop_pending_automation("telegram"))
        self.assertTrue(o._has_pending_automation("desktop"))
        self.assertIs(o._pop_pending_automation("desktop"), _HIGH)
        self.assertIsNone(o._pop_pending_automation("desktop"))

    def test_pending_expires(self):
        o = _bare_orch()
        o._set_pending_automation("desktop", _HIGH)
        o._pending_automations["desktop"]["ts"] -= o._PENDING_AUTOMATION_TTL_SECS + 1
        self.assertFalse(o._has_pending_automation("desktop"))
        self.assertIsNone(o._pop_pending_automation("desktop"))

    async def test_yes_confirms_only_own_session(self):
        o = _bare_orch()
        o._set_pending_automation("desktop", _HIGH)
        result = await o.handle_input("sí", _ctx("desktop"))
        self.assertEqual(result, "ejecutado")
        o.automation.execute.assert_awaited_once_with(_HIGH)

    async def test_no_cancels(self):
        o = _bare_orch()
        o._set_pending_automation("desktop", _HIGH)
        result = await o.handle_input("no", _ctx("desktop"))
        self.assertIn("no ejecutaré", result)
        o.automation.execute.assert_not_awaited()

    async def test_implicit_yes_never_reruns_high_risk(self):
        o = _bare_orch(last_automation={"analysis": _HIGH, "ts": time.time()})
        result = await o.handle_input("dale", _ctx("telegram"))
        self.assertIn("confirmación", result)
        o.automation.execute.assert_not_awaited()
        # Quedó encolada para ESA sesión: el siguiente "sí" la ejecuta.
        self.assertTrue(o._has_pending_automation("telegram"))

    async def test_implicit_yes_still_reruns_safe_action(self):
        o = _bare_orch(last_automation={"analysis": _SAFE, "ts": time.time()})
        result = await o.handle_input("dale", _ctx("desktop"))
        self.assertEqual(result, "ejecutado")
        o.automation.execute.assert_awaited_once_with(_SAFE)


class TestExecuteCandidateGuard(unittest.IsolatedAsyncioTestCase):

    async def test_high_risk_candidate_asks_instead_of_executing(self):
        o = _bare_orch()
        cand = MagicMock(kind="router", payload=_HIGH)
        result = await o._execute_candidate("comprá el mouse", cand, _ctx("desktop"))
        self.assertIn("confirmación", result)
        o.automation.execute.assert_not_awaited()
        self.assertTrue(o._has_pending_automation("desktop"))

    async def test_safe_candidate_executes(self):
        o = _bare_orch()
        cand = MagicMock(kind="router", payload=_SAFE)
        self.assertEqual(await o._execute_candidate("abrí youtube", cand, _ctx("desktop")), "ejecutado")


if __name__ == "__main__":
    unittest.main()
