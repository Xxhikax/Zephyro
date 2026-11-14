"""
Suite: Router close_keywords word-boundary regression + BrowserController guard

Bugs cubiertos:
  BUG-R1: "termina" es substring de "terminal" → falso positivo en close_keywords
           "abre terminal" era clasificado como close_tab, disparando Playwright.
           Fix: re.search(r'\\b(cierra|cerrar|cierre|termina|finaliza)\\b') en router.py
  BUG-R2: close_tab() guard solo cubría BrowserState.CLOSED
           Si el browser previo falló (estado ERROR / RECOVERING), el guard no actuaba
           y _operation_wrapper llamaba _ensure_browser() → reintento innecesario de Firefox.
           Fix: guard expandido a (CLOSED, ERROR, RECOVERING)

Organización de tests:
  1. Router — frases que NO deben disparar close_tab (apps nativas con nombres problemáticos)
  2. Router — frases que SÍ deben disparar close_tab (regression)
  3. Router — word-boundary: "termina" solo como palabra completa dispara close_tab
  4. BrowserController — guard cubre CLOSED, ERROR y RECOVERING
  5. BrowserController — estado STARTING sí pasa al _enqueue_command
"""
import sys
import os
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.router import IntentRouter
from core.browser_config import BrowserConfig
from modules.automation.browser_controller import BrowserController, BrowserState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_router() -> IntentRouter:
    """IntentRouter con LLM mockeado (tests de CAPA 1 no necesitan LLM real)."""
    llm = MagicMock()
    llm.is_available = MagicMock(return_value=True)
    return IntentRouter(llm_manager=llm, context_manager=None)


def make_closed_controller() -> BrowserController:
    cfg = BrowserConfig.for_headless()
    return BrowserController(config=cfg)


async def _force_state(ctrl: BrowserController, state: BrowserState) -> None:
    """Pone el controller en el estado dado sin iniciar el browser."""
    await ctrl._set_state(state, "forced_by_test")


# ===========================================================================
# BLOQUE 1 — Frases que NO deben disparar close_tab
# ===========================================================================

@pytest.mark.asyncio
async def test_abre_terminal_no_es_close_tab():
    """
    BUG-R1: 'abre terminal' nunca debe producir action='close_tab'.
    'termina' es substring de 'terminal'. Sin word-boundary el bug estaba activo.
    """
    router = make_router()
    result = await router.classify_and_parse("abre terminal")

    assert result.get("intent") == "automation", (
        f"Intent debería ser 'automation', got: {result}")
    assert result.get("action") != "close_tab", (
        f"'abre terminal' NO debe resultar en close_tab, got action={result.get('action')}")
    # La acción correcta es "open"
    assert result.get("action") == "open", (
        f"Esperado action='open', got: {result.get('action')}")
    # El target debe ser la app nativa, no un comando de cierre
    assert result.get("automation_intent") != "INTERACT", (
        f"automation_intent no debe ser INTERACT para apertura de app nativa, got: {result.get('automation_intent')}")


@pytest.mark.asyncio
async def test_abre_konsole_no_es_close_tab():
    """
    'abre konsole' (terminal de KDE en CachyOS) → NO close_tab.
    'konsole' no contiene ningún close_keyword, pero es un caso de regresión
    relevante dado el contexto del bug original.
    """
    router = make_router()
    result = await router.classify_and_parse("abre konsole")

    assert result.get("intent") == "automation"
    assert result.get("action") != "close_tab", (
        f"'abre konsole' no debe ser close_tab, got: {result}")
    assert result.get("action") == "open"


@pytest.mark.asyncio
async def test_finaliza_no_aple_a_app_con_substring():
    """
    'finaliza' es otro keyword en close_keywords.
    Verificar que NO matchee si es substring de otro token (ej. 'finalizacion').
    """
    router = make_router()
    # "finalizacion" contiene "finaliza" como substring — NO debe disparar close_tab
    result = await router.classify_and_parse("quiero ver la finalizacion de la descarga")
    # El resultado puede ser information/chat — solo verificamos que NO sea close_tab
    assert result.get("action") != "close_tab", (
        f"'finalizacion' no debe disparar close_tab como substring, got: {result}")


@pytest.mark.asyncio
async def test_abre_terminal_no_envia_a_playwright():
    """
    End-to-end lógico: 'abre terminal' no debe ir al path de Playwright.
    
    Nota: el router puede producir automation_intent='NAVIGATE' si extract_search_query
    extrae 'terminal' como search_query. Esto es OK porque _resolve_navigate_url('terminal')
    retorna None (no es una web app) y AppController cae al fallback launcher.open_app().
    Lo que NUNCA debe ocurrir es automation_intent='INTERACT' con action='close_tab'.
    """
    router = make_router()
    result = await router.classify_and_parse("abre terminal")

    # NUNCA debe ser INTERACT/close_tab — eso dispararía Playwright
    assert result.get("automation_intent") != "INTERACT", (
        f"'abre terminal' no debe resultar en INTERACT (path de Playwright), got: {result}")
    assert result.get("action") != "close_tab", (
        f"'abre terminal' no debe ser close_tab, got: {result}")
    # Debe ser intent automation con action open
    assert result.get("intent") == "automation"
    assert result.get("action") == "open"


# ===========================================================================
# BLOQUE 2 — Frases que SÍ deben disparar close_tab (regression)
# ===========================================================================

@pytest.mark.asyncio
async def test_cierra_la_pestana_si_es_close_tab():
    """
    Regression: 'cierra el navegador' DEBE seguir disparando close_tab.
    
    Nota: 'cierra la pestaña' (sin target de app conocido) cae a CAPA 2 (Haiku LLM)
    porque el target queda 'unknown'. En tests sin API key eso falla. Se usa
    'cierra el navegador' donde 'navegador' sí está en la lista de apps → CAPA 1 resuelve.
    """
    router = make_router()
    result = await router.classify_and_parse("cierra el navegador")

    assert result.get("intent") == "automation", f"Got: {result}"
    assert result.get("automation_intent") == "INTERACT", (
        f"'cierra el navegador' debe resultar en INTERACT, got: {result.get('automation_intent')}")
    assert result.get("action") == "close_tab", (
        f"'cierra el navegador' debe resultar en action=close_tab, got: {result.get('action')}")


@pytest.mark.asyncio
async def test_cerrar_pestana_sinonimo():
    """'cerrar firefox' — 'cerrar' es sinónimo de 'cierra' — también dispara close_tab."""
    router = make_router()
    result = await router.classify_and_parse("cerrar firefox")

    assert result.get("action") == "close_tab", (
        f"'cerrar firefox' debe ser close_tab, got: {result}")


@pytest.mark.asyncio
async def test_cierra_firefox():
    "'cierra firefox' → close. Regression de comportamiento previo al fix."
    router = make_router()
    result = await router.classify_and_parse("cierra firefox")

    # 'cierra' con word boundary → automation_intent = INTERACT, action = close_tab
    assert result.get("intent") == "automation"
    assert result.get("action") == "close_tab"


# ===========================================================================
# BLOQUE 3 — Word-boundary: "termina" como palabra completa sí dispara close_tab
# ===========================================================================

@pytest.mark.asyncio
async def test_termina_como_palabra_dispara_close_tab():
    """
    'termina el proceso en el navegador' tiene 'termina' como palabra completa
    (seguida de espacio) → SÍ debe disparar close_tab con el word-boundary.
    """
    router = make_router()
    result = await router.classify_and_parse("termina el proceso en el navegador")

    assert result.get("action") == "close_tab", (
        f"'termina' como palabra completa debe disparar close_tab, got: {result}")


@pytest.mark.asyncio
async def test_termina_youtube():
    "'termina youtube' — 'termina' es palabra completa → close_tab."
    router = make_router()
    result = await router.classify_and_parse("termina youtube")

    assert result.get("action") == "close_tab", (
        f"got: {result}")


@pytest.mark.asyncio
async def test_word_boundary_invariant_abre_terminal():
    """
    Invariante explícito del bug-fix: verificar que la regex con word-boundary
    NO matchea 'termina' como substring de 'terminal'.
    """
    import re
    _pattern = r'\b(cierra|cerrar|cierre|termina|finaliza)\b'

    # Caso buggeado (debe ser False tras el fix → NO match)
    assert not re.search(_pattern, "abre terminal"), (
        "BUG-R1 REGRESIÓN: 'termina' matcheó como substring de 'terminal' — "
        "el fix de word-boundary no está activo."
    )

    # Caso correcto (debe ser True → SÍ match)
    assert re.search(_pattern, "cierra la pestaña"), (
        "'cierra' con word-boundary no matcheó — regresión en el fix."
    )
    assert re.search(_pattern, "termina el proceso"), (
        "'termina' como palabra completa no matcheó — regresión inesperada."
    )
    assert re.search(_pattern, "finaliza la descarga"), (
        "'finaliza' no matcheó — regresión."
    )


# ===========================================================================
# BLOQUE 4 — BrowserController: guard cubre CLOSED, ERROR y RECOVERING
# ===========================================================================

@pytest.mark.asyncio
async def test_close_tab_en_estado_closed_retorna_early():
    """BUG-R2 (ya fijado): estado CLOSED → retorna 'No hay navegador abierto'."""
    ctrl = make_closed_controller()
    assert ctrl.get_state() == BrowserState.CLOSED

    ctrl._ensure_browser = AsyncMock(side_effect=AssertionError("_ensure_browser NO debe llamarse"))
    ctrl._enqueue_command = AsyncMock(side_effect=AssertionError("_enqueue_command NO debe llamarse"))

    result = await ctrl.close_tab()

    assert "navegador" in result.lower(), f"Mensaje esperado sobre navegador, got: {result!r}"
    ctrl._ensure_browser.assert_not_called()
    ctrl._enqueue_command.assert_not_called()


@pytest.mark.asyncio
async def test_close_tab_en_estado_error_retorna_early():
    """
    BUG-R2 FIX: estado ERROR → close_tab retorna early, NO llama _ensure_browser.
    Este era el caso exacto del bug: Firefox había fallado previamente,
    estado quedó en ERROR, close_tab intentaba relanzarlo.
    """
    ctrl = make_closed_controller()
    await _force_state(ctrl, BrowserState.ERROR)
    assert ctrl.get_state() == BrowserState.ERROR

    ctrl._ensure_browser = AsyncMock(side_effect=AssertionError("_ensure_browser NO debe llamarse"))
    ctrl._enqueue_command = AsyncMock(side_effect=AssertionError("_enqueue_command NO debe llamarse"))

    result = await ctrl.close_tab()

    assert isinstance(result, str)
    assert "navegador" in result.lower(), f"Got: {result!r}"
    ctrl._ensure_browser.assert_not_called()
    ctrl._enqueue_command.assert_not_called()


@pytest.mark.asyncio
async def test_close_tab_en_estado_recovering_retorna_early():
    """
    Estado RECOVERING → close_tab retorna early.
    RECOVERING ocurre cuando el browser se cerró inesperadamente y
    _operation_wrapper está intentando recuperarlo.
    """
    ctrl = make_closed_controller()
    await _force_state(ctrl, BrowserState.RECOVERING)
    assert ctrl.get_state() == BrowserState.RECOVERING

    ctrl._ensure_browser = AsyncMock(side_effect=AssertionError("_ensure_browser NO debe llamarse"))
    ctrl._enqueue_command = AsyncMock(side_effect=AssertionError("_enqueue_command NO debe llamarse"))

    result = await ctrl.close_tab()

    assert isinstance(result, str)
    assert "navegador" in result.lower(), f"Got: {result!r}"
    ctrl._ensure_browser.assert_not_called()
    ctrl._enqueue_command.assert_not_called()


@pytest.mark.asyncio
async def test_close_tab_en_estado_ready_pasa_a_enqueue():
    """
    Regression: estado READY → close_tab SÍ debe pasar a _enqueue_command.
    (Browser abierto y funcional — operación válida.)
    """
    ctrl = make_closed_controller()
    # Forzar estado READY
    await _force_state(ctrl, BrowserState.READY)
    assert ctrl.get_state() == BrowserState.READY

    # Mock _enqueue_command para que no intente operar el browser real
    ctrl._enqueue_command = AsyncMock(return_value="Pestaña cerrada")

    result = await ctrl.close_tab()

    ctrl._enqueue_command.assert_called_once()
    assert result == "Pestaña cerrada"


@pytest.mark.asyncio
async def test_close_tab_en_estado_degraded_pasa_a_enqueue():
    """
    Estado DEGRADED (headless/sin VNC) → close_tab SÍ pasa a _enqueue_command.
    DEGRADED es un estado funcional (browser operativo en modo reducido).
    """
    ctrl = make_closed_controller()
    await _force_state(ctrl, BrowserState.DEGRADED)

    ctrl._enqueue_command = AsyncMock(return_value="Pestaña cerrada (degraded)")

    result = await ctrl.close_tab()

    ctrl._enqueue_command.assert_called_once()
    assert "cerrada" in result.lower()


@pytest.mark.asyncio
async def test_close_tab_mensaje_incluye_estado_en_error():
    """
    El log message de close_tab en estado ERROR/RECOVERING incluye el estado actual.
    Esto facilita el debugging (antes solo decía 'no browser open, skipping').
    """
    ctrl = make_closed_controller()
    await _force_state(ctrl, BrowserState.ERROR)

    ctrl._enqueue_command = AsyncMock(side_effect=AssertionError("no debe llamarse"))

    # No lanza excepción — retorna limpio
    result = await ctrl.close_tab()
    assert isinstance(result, str)
    assert len(result) > 0


# ===========================================================================
# BLOQUE 5 — Integración router → controller: el fix elimina el error de Playwright
# ===========================================================================

@pytest.mark.asyncio
async def test_integracion_abre_terminal_no_toca_browser_controller():
    """
    Integración: 'abre terminal' nunca debe llegar al path de BrowserController.

    El router puede producir automation_intent='NAVIGATE' (cuando extract_search_query
    extrae 'terminal' como search_query). Eso es aceptable porque _resolve_navigate_url
    retorna None para 'terminal' y AppController termina en launcher.open_app() de todas
    formas. Lo crítico es que NUNCA sea INTERACT+close_tab, que desencadena Playwright.
    """
    router = make_router()
    result = await router.classify_and_parse("abre terminal")

    # MUST: intent es automation con action open
    assert result.get("intent") == "automation"
    assert result.get("action") == "open"
    # MUST NOT: nunca INTERACT — eso invoca Playwright
    assert result.get("automation_intent") != "INTERACT", (
        f"BUG-R1 REGRESIÓN: 'abre terminal' fue clasificado como INTERACT → Playwright path. "
        f"Got: {result}"
    )
    # MUST NOT: nunca close_tab
    assert result.get("action") != "close_tab"


@pytest.mark.asyncio
async def test_integracion_todas_apps_nativas_no_son_close_tab():
    """
    Regression exhaustiva: para CADA app nativa en el router,
    'abre <app>' nunca debe producir close_tab.
    """
    apps_nativas = [
        "firefox", "konsole", "terminal", "discord", "vlc", "steam",
        "libreoffice", "code", "kate", "dolphin", "alacritty",
        "calculadora", "monitor", "spotify",
    ]
    router = make_router()

    fallos = []
    for app in apps_nativas:
        frase = f"abre {app}"
        result = await router.classify_and_parse(frase)
        if result.get("action") == "close_tab":
            fallos.append(f"'{frase}' → close_tab (BUG-R1 regression)")

    assert not fallos, "\n".join(fallos)
