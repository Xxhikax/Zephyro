"""Pedir cerrar algo tiene que cerrar ESO, y pasar por el guardrail.

Tres bugs encadenados, medidos el 2026-10-02 y encontrados por una review de
Codex sobre el diff de la Fase A:

1. El router reescribía TODO verbo de cierre a `INTERACT/close_tab`, pisando
   el `action_value="close"` calculado ~340 líneas antes. El ejecutor de
   `close_tab` (`app_controller.py:542`) hace
   `return await self.browser.close_tab()` e IGNORA el target: "cerrá word"
   cerraba una pestaña del navegador, no Word.

2. Como el guardrail evalúa `action == "close"`, NUNCA se alcanzaba desde el
   camino regex — con su lista de procesos protegidos y de apps que sostienen
   trabajo sin guardar incluidas. `cierra word` salía `reversible` y se
   ejecutaba sin preguntar.

3. El voseo `cerrá` no estaba en la regex de cierre (sí en `core/verbs.py`,
   que es lo que lo hace entrar como comando), así que quedaba el default
   `action="open"` → **pedir cerrar Word lo ABRÍA**, 4 de 4 apps probadas.
   Causa: el router era el único consumidor de `core/verbs.py` que no llamaba
   a `normalize_voseo`; `skills/registry.py` sí lo hace, en sus dos puntos de
   dispatch.
"""
import asyncio

import pytest

from core.action_risk_rules import ActionRisk, classify_action
from core.router import IntentRouter


class _Ctx:
    """ContextManager mínimo con una automatización previa."""

    def __init__(self, payload=None):
        self._last = {"analysis": payload} if payload else None

    def get_last_automation(self):
        return self._last

    def set_last_automation(self, v):
        self._last = v

    def add(self, *a, **k):
        pass


_PREV_BROWSER = {"intent": "automation", "action": "open", "target": "firefox"}


def _route(phrase, prev=None):
    r = IntentRouter(None, context_manager=_Ctx(prev))
    return asyncio.run(r.classify_and_parse(phrase, skip_llm=True))


# ── Cerrar una app NO abre la app ───────────────────────────────────────
@pytest.mark.parametrize("phrase", [
    "cerrá word", "cerra word", "cierra word",
    "cerrá excel", "cerrá chrome", "cerrá discord",
])
def test_asking_to_close_never_opens(phrase):
    a = _route(phrase)
    assert a.get("action") != "open", (
        f"{phrase!r} pide CERRAR y el router devolvio action=open"
    )


# ── Cerrar una app pasa por el guardrail ────────────────────────────────
@pytest.mark.parametrize("phrase", [
    "cierra word", "cerrá word", "cerra word", "finaliza word",
    "cierra excel", "termina el informe de Word", "mata chrome",
])
def test_closing_an_app_with_documents_asks_first(phrase):
    """`close_app` mata el proceso: no hay dialogo de recuperacion."""
    a = _route(phrase)
    v = classify_action(a)
    assert v.level is ActionRisk.HIGH, f"{phrase!r} -> {a}"
    assert v.needs_confirmation


@pytest.mark.parametrize("phrase", ["cierra spotify", "cerrá discord"])
def test_closing_an_app_without_documents_does_not_nag(phrase):
    a = _route(phrase)
    assert a.get("action") == "close"
    assert classify_action(a).level is ActionRisk.REVERSIBLE


# ── Lo que SÍ es una pestaña sigue siendo una pestaña ───────────────────
@pytest.mark.parametrize("phrase", [
    "cierra la pestaña", "cerra la pestaña", "cierra esta pestaña",
])
def test_a_tab_noun_still_closes_a_tab(phrase):
    a = _route(phrase, _PREV_BROWSER)
    assert a.get("action") == "close_tab", f"{phrase!r} -> {a}"


@pytest.mark.parametrize("phrase,why", [
    ("cierra chrome", "el navegador es lo que Zephyro automatiza"),
    ("cierra el navegador", "idem, nombrado en generico"),
    ("termina youtube", "es un SITIO: matar un proceso 'youtube' no existe"),
])
def test_browsers_and_sites_close_as_a_tab(phrase, why):
    a = _route(phrase)
    assert a.get("action") == "close_tab", f"{phrase!r} ({why}) -> {a}"


def test_a_native_app_wins_over_the_same_name_as_a_site():
    """`spotify` esta en NATIVE_APPS y tambien en SITE_KEYWORDS."""
    a = _route("cierra spotify")
    assert a.get("action") == "close", (
        "'cerra spotify' se refiere al programa, no a una pestaña"
    )


# ── El router normaliza voseo, igual que las skills ─────────────────────
@pytest.mark.parametrize("voseo,tuteo", [
    ("cerrá word", "cierra word"),
    ("abrí discord", "abre discord"),
    ("terminá word", "termina word"),
])
def test_voseo_routes_like_its_tuteo_form(voseo, tuteo):
    """El router era el unico consumidor de verbs.py que no normalizaba."""
    a_vos, a_tut = _route(voseo), _route(tuteo)
    assert a_vos.get("action") == a_tut.get("action"), f"{a_vos} != {a_tut}"
    assert a_vos.get("target") == a_tut.get("target")
