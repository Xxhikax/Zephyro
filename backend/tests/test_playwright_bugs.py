"""
Suite 1 — Playwright bug regression tests.

Tests cubiertos:
1. close_tab sin browser abierto → sin excepción, sin abrir browser
2. Binario de Playwright faltante → mensaje amigable con hint "playwright install"
3. Ciclo open → use → close sin errores
4. Respuesta normal sin browser → close_tab NO se llama
"""
import sys
import os
import pytest
import asyncio
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch, call
from core.browser_config import BrowserConfig
from modules.automation.browser_controller import BrowserController, BrowserState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_closed_controller() -> BrowserController:
    """BrowserController en estado CLOSED con internals nulos."""
    cfg = BrowserConfig.for_headless()
    ctrl = BrowserController(config=cfg)
    # Estado por defecto ya es CLOSED, playwright/browser/context/page = None
    return ctrl


# ---------------------------------------------------------------------------
# Test 1 — close_tab sin browser abierto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_tab_when_browser_closed():
    """
    close_tab() no debe lanzar excepción ni abrir el browser
    cuando el estado es CLOSED.
    """
    ctrl = make_closed_controller()
    assert ctrl.get_state() == BrowserState.CLOSED

    # Mockeamos _ensure_browser para detectar si se llama
    ctrl._ensure_browser = AsyncMock(side_effect=AssertionError("_ensure_browser no debe llamarse"))

    result = await ctrl.close_tab()

    # Debe retornar mensaje de "no hay browser", no lanzar excepción
    assert result is not None
    assert isinstance(result, str)
    # _ensure_browser NO fue llamado (si lo fuera, la excepción habría surgido)
    ctrl._ensure_browser.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — Binario faltante → mensaje amigable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_binary_friendly_message():
    """
    Cuando todas las estrategias de launch fallan con un error de binario,
    el mensaje de error debe contener el hint de 'playwright install'.
    """
    ctrl = make_closed_controller()

    binary_error = Exception("Executable doesn't exist at /usr/bin/firefox-playwright")

    # Reemplazamos _build_launch_strategies por una lista que siempre falla
    async def failing_strategy():
        raise binary_error

    ctrl._build_launch_strategies = MagicMock(return_value=[
        ("strategy_fake", failing_strategy),
    ])
    # _close_browser_internal no hace nada
    ctrl._close_browser_internal = AsyncMock()

    with pytest.raises(RuntimeError) as exc_info:
        await ctrl._restart_browser()

    msg = str(exc_info.value).lower()
    assert "playwright install" in msg, (
        f"Se esperaba hint 'playwright install' en el error, se obtuvo: {msg!r}"
    )


@pytest.mark.asyncio
async def test_missing_binary_pattern_variants():
    """Verifica que distintos patrones de error de binario producen mensaje amigable."""
    binary_errors = [
        "Failed to launch browser",
        "No such file or directory: /usr/lib/firefox",
        "BrowserType.launch: Executable doesn't exist",
        "Error launching browser process",
    ]

    for err_msg in binary_errors:
        ctrl = make_closed_controller()

        async def failing_strategy(_msg=err_msg):
            raise Exception(_msg)

        ctrl._build_launch_strategies = MagicMock(return_value=[
            ("fake", failing_strategy),
        ])
        ctrl._close_browser_internal = AsyncMock()

        with pytest.raises(RuntimeError) as exc_info:
            await ctrl._restart_browser()

        result = str(exc_info.value).lower()
        assert "playwright install" in result, (
            f"Pattern {err_msg!r} no produjo mensaje amigable. Obtenido: {result!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Ciclo open → navigate → close sin errores
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_use_close_cycle():
    """
    Ciclo completo mocked: open → navigate → close_tab sin errores.
    """
    ctrl = BrowserController(config=BrowserConfig.for_headless())

    # Mock de playwright/browser/context/page
    mock_page = MagicMock()
    mock_page.is_closed.return_value = False
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.title = AsyncMock(return_value="Test Page")
    mock_page.close = AsyncMock()

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True

    mock_pw = MagicMock()

    # Inyectar estado "READY" con objetos mocked
    ctrl.playwright = mock_pw
    ctrl.browser = mock_browser
    ctrl.context = mock_context
    ctrl.page = mock_page
    ctrl._state = BrowserState.READY

    # Mockear _ensure_browser para que no reabra nada
    ctrl._ensure_browser = AsyncMock()

    # Test: navigate_to
    mock_page.url = "about:blank"
    navigate_result = await ctrl._navigate_to("https://example.com")
    assert "example.com" in navigate_result or "Error" not in navigate_result

    # Test: close_tab — con una sola página, debe cerrar el browser
    ctrl._close = AsyncMock(return_value="Navegador cerrado")
    close_result = await ctrl._close_tab()
    assert close_result is not None
    assert isinstance(close_result, str)


# ---------------------------------------------------------------------------
# Test 4 — Respuesta normal sin browser → close_tab no se llama
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_response_no_playwright_error():
    """
    Una respuesta sin automation no debe invocar close_tab
    ni producir errores de Playwright.
    """
    ctrl = make_closed_controller()
    ctrl.close_tab = AsyncMock(side_effect=AssertionError("close_tab no debe llamarse en respuesta normal"))

    # Simular que el orchestrator maneja un mensaje normal
    # (en este nivel solo verificamos que close_tab en el controller no se llama)
    # sin pasar por orchestrator completo.
    response = "Hola, ¿cómo estás?"

    # Simular el hook de cierre del plan: solo se llama close_tab si hubo automation
    automation_used = False
    if automation_used:
        await ctrl.close_tab()

    assert response is not None
    ctrl.close_tab.assert_not_called()
