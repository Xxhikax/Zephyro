"""Instagram se abría en un perfil de Edge aparte, con otra sesión.

Bruno: "lo abre en un perfil de edge aparte cosa que no quiero".

`PLAYWRIGHT_CAPABLE_SITES` (app_config.py) listaba instagram, youtube,
twitter, reddit, tiktok… y `app_controller` tenía UNA sola decisión:

    if target in PLAYWRIGHT_CAPABLE_SITES:   ->  Playwright
    else:                                    ->  navegador nativo

O sea que miraba el DOMINIO, no el PEDIDO. El comentario del código lo
justificaba con "para que la pestaña quede bajo control de Playwright y los
comandos INTERACT SIGUIENTES puedan operar sobre ella": pagaba en CADA
navegación por una capacidad que hace falta A VECES. Y como el Edge real no
arranca con el puerto de depuración, el attach fallaba siempre y todo caía en
el perfil dedicado `EdgeAutomation`.

Ahora navegar es navegar: va al navegador del usuario, con su sesión.
Playwright entra en INTERACT y PLAYBACK_CONTROL, que son los que tocan la
página.

**La condición que hace aceptable el trade-off:** si después pedís algo que
necesita control del DOM sobre una pestaña que abrimos de forma nativa, hay
que DECIRLO. Actuar igual correría contra otra página en otro perfil en
silencio — "listo" sin que pase nada. Eso es peor que fallar.

⚠️ Estos tests NUNCA construyen `AppController()` entero: su `__init__` llega
a `BrowserConfig`, que en Windows hace CDP attach al Edge REAL de Bruno y lo
puede dejar zombie (ver `.claude/rules/tests-safety.md`).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.app_config import PLAYWRIGHT_CAPABLE_SITES
from modules.automation.app_controller import AppController


def _controller():
    """AppController sin tocar Playwright ni BrowserConfig."""
    c = AppController.__new__(AppController)
    c.launcher = MagicMock()
    c._browser = MagicMock()
    c._browser_config = None
    c.planner = MagicMock()
    c._tab_control = None
    c._tab_url = None
    return c


def _nav(target, sub_path=None):
    a = {
        "intent": "automation",
        "automation_intent": "NAVIGATE",
        "action": "open",
        "target": target,
    }
    if sub_path:
        a["sub_path"] = sub_path
    return a


# ── Navegar va a TU navegador ───────────────────────────────────────────
@pytest.mark.parametrize("target,sub_path", [
    ("instagram", None),
    ("instagram", "reels"),
    ("youtube", None),
    ("reddit", None),
    ("twitter", None),
])
def test_navigation_goes_to_the_users_own_browser(target, sub_path):
    """Incluso para los sitios que antes forzaban Playwright."""
    assert target in PLAYWRIGHT_CAPABLE_SITES, (
        f"{target} ya no esta en la lista: el test perdio su sentido"
    )
    c = _controller()
    c._open_url_async = AsyncMock(return_value="✓ Abriendo en nueva pestaña de msedge")
    c.browser.navigate_to = AsyncMock(return_value="NO DEBERIA LLAMARSE")
    c._publish_navigate_url = MagicMock()

    out = asyncio.run(c._execute_inner(_nav(target, sub_path)))

    assert c._open_url_async.called, f"{target} no fue al navegador nativo"
    assert not c.browser.navigate_to.called, (
        f"{target} siguio yendo por Playwright (perfil aparte)"
    )
    assert "msedge" in out
    assert c._tab_control == "native"


def test_the_sub_path_survives_the_native_route():
    """"entra a reels" tiene que terminar en instagram.com/reels."""
    c = _controller()
    c._open_url_async = AsyncMock(return_value="ok")
    c._publish_navigate_url = MagicMock()

    asyncio.run(c._execute_inner(_nav("instagram", "reels")))

    url = c._open_url_async.call_args[0][0]
    assert url.rstrip("/").endswith("/reels"), url


# ── Y se dice la verdad cuando no se puede tocar esa pestaña ────────────
def test_interacting_with_a_native_tab_is_refused_not_faked():
    """El costo del trade-off, dicho en voz alta en vez de fallar en silencio."""
    c = _controller()
    c._tab_control, c._tab_url = "native", "https://instagram.com/reels"
    c.planner.build_plan = MagicMock(return_value=[{"action": "click"}])
    c._execute_atomic_action = AsyncMock(return_value="click hecho")

    out = asyncio.run(c._execute_inner({
        "intent": "automation",
        "automation_intent": "INTERACT",
        "action": "click",
        "target": "seguir",
    }))

    assert not c._execute_atomic_action.called, (
        "corrio el click contra OTRA pagina en OTRO perfil, en silencio"
    )
    assert "tu Edge" in out
    assert "instagram.com/reels" in out, "no dice de que pagina habla"


def test_interacting_with_our_own_tab_still_works():
    """Contraparte legítima: si la pestaña la abrió Playwright, se actúa."""
    c = _controller()
    c._tab_control = "owned"
    c.planner.build_plan = MagicMock(return_value=[{"action": "click"}])
    c._execute_atomic_action = AsyncMock(return_value="ok")

    out = asyncio.run(c._execute_inner({
        "intent": "automation",
        "automation_intent": "INTERACT",
        "action": "click",
        "target": "seguir",
    }))

    assert c._execute_atomic_action.called
    assert "tu Edge" not in out


def test_without_any_previous_navigation_nothing_is_blocked():
    """Un INTERACT que abre su propia página no tiene por qué ser rechazado."""
    c = _controller()
    assert c._tab_control is None
    c.planner.build_plan = MagicMock(return_value=[{"action": "click"}])
    c._execute_atomic_action = AsyncMock(return_value="ok")

    asyncio.run(c._execute_inner({
        "intent": "automation",
        "automation_intent": "INTERACT",
        "action": "click",
        "target": "algo",
    }))

    assert c._execute_atomic_action.called


# ── El default que podía matarte el navegador ───────────────────────────
def test_navigating_can_no_longer_relaunch_your_real_edge():
    """`_restart_browser` llama sin argumentos: el default ES la política.

    Con True, un "abre instagram" podía cerrar el Edge de Bruno —con sus
    pestañas— para poder automatizar. Matar el navegador de alguien es un
    efecto mucho más grande que el comando que lo pidió.
    """
    import inspect

    from modules.automation.browser_controller import BrowserController

    sig = inspect.signature(BrowserController._launch_cdp_attach)
    assert sig.parameters["allow_real_edge_relaunch"].default is False
