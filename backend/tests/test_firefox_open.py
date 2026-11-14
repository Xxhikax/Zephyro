import pytest
import asyncio
from pathlib import Path

from core.browser_config import BrowserConfig
from modules.automation.browser_controller import BrowserController


@pytest.mark.asyncio
async def test_open_firefox_headless():
    """Test basico: abrir Firefox headless y navegar a example.com."""
    config = BrowserConfig.for_headless()
    controller = BrowserController(config=config)

    try:
        result = await controller.navigate_to("https://example.com")
        assert "Error" not in result, f"Navegacion fallo: {result}"
        assert controller.page is not None
        assert not controller.page.is_closed()
    finally:
        await controller._close()


@pytest.mark.asyncio
async def test_open_firefox_visible():
    """Test: abrir Firefox visible y navegar."""
    config = BrowserConfig.for_visible()
    controller = BrowserController(config=config)

    try:
        result = await controller.navigate_to("https://example.com")
        assert "Error" not in result, f"Navegacion fallo: {result}"
    finally:
        await controller._close()


@pytest.mark.asyncio
async def test_browser_config_auto_detect():
    """Test: BrowserConfig auto-detecta Firefox y perfil."""
    config = BrowserConfig()
    assert config.firefox_executable is not None, "No se detecto Firefox"
    assert Path(config.firefox_executable).exists(), f"Firefox no existe: {config.firefox_executable}"


@pytest.mark.asyncio
async def test_url_validation():
    """Test: validacion de URLs."""
    config = BrowserConfig.for_headless()
    controller = BrowserController(config=config)

    # URL valida
    url = controller._validate_url("example.com")
    assert url == "https://example.com"

    # URL ya completa
    url = controller._validate_url("https://youtube.com")
    assert url == "https://youtube.com"

    # URL invalida
    with pytest.raises(ValueError):
        controller._validate_url("")

    with pytest.raises(ValueError):
        controller._validate_url("   ")
