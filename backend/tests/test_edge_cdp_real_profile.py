"""
Tests para verificar que Zephyro usa el perfil REAL de Edge vía CDP attach.

Verifica:
1. BrowserConfig apunta al perfil real (%LOCALAPPDATA%\\Microsoft\\Edge\\User Data),
   no a un clon ni a un perfil dedicado.
2. No se crean directorios de clon (edge_profile_clone, browser_profile).
3. La única estrategia de lanzamiento es CDP attach.
4. _launch_cdp_attach hace connect_over_cdp cuando el puerto está vivo, sin
   tocar Edge en absoluto.
5. _launch_cdp_attach usa la instancia dedicada de automatización cuando el
   puerto del Edge real NO está vivo (sin tocar el Edge real).
6. _launch_cdp_attach solo mata/relanza el Edge real como último fallback,
   cuando la instancia dedicada falla y el llamador lo permite.
7. _close_browser_internal NO cierra el contexto del usuario en modo CDP.
"""
import os
import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# -------------------------------------------------------------------
# 1. BrowserConfig usa el perfil real de Edge
# -------------------------------------------------------------------

def test_browser_config_points_to_real_edge_profile():
    from core.browser_config import BrowserConfig

    cfg = BrowserConfig()

    assert cfg.user_profile_path is not None, "user_profile_path debe estar resuelto"
    expected = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data"
    assert Path(cfg.user_profile_path) == expected, (
        f"Esperado {expected}, obtenido {cfg.user_profile_path}"
    )
    # Asegurar que NO es un clon o perfil dedicado
    assert "clone" not in cfg.user_profile_path.lower()
    assert "browser_profile" not in cfg.user_profile_path.lower()


def test_no_clone_directory_is_created():
    """Tras instanciar BrowserConfig, no debe existir backend/storage/edge_profile_clone."""
    from core.browser_config import BrowserConfig

    BrowserConfig()
    clone_dir = Path("backend/storage/edge_profile_clone")
    sandbox_dir = Path("backend/storage/browser_profile")
    assert not clone_dir.exists(), f"No debería existir: {clone_dir}"
    assert not sandbox_dir.exists(), f"No debería existir: {sandbox_dir}"


def test_browser_config_exposes_cdp_port():
    from core.browser_config import BrowserConfig
    cfg = BrowserConfig()
    assert isinstance(cfg.cdp_port, int) and cfg.cdp_port > 0


# -------------------------------------------------------------------
# 2. La única estrategia de lanzamiento es CDP attach
# -------------------------------------------------------------------

def test_only_cdp_strategy_is_used():
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig

    bc = BrowserController(BrowserConfig())
    strategies = bc._build_launch_strategies()
    assert len(strategies) == 1
    name, fn = strategies[0]
    assert name == "cdp_attach_real_edge"
    assert fn == bc._launch_cdp_attach


# -------------------------------------------------------------------
# 3. _launch_cdp_attach: caminos de control
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cdp_attach_when_port_already_alive():
    """Si Edge ya expone debug port, conectamos sin tocar nada más."""
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig

    bc = BrowserController(BrowserConfig())

    fake_page = MagicMock(name="page")
    fake_page.is_closed = MagicMock(return_value=False)
    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_browser = MagicMock()
    fake_browser.contexts = [fake_context]
    fake_browser.new_context = AsyncMock(return_value=fake_context)

    bc.playwright = MagicMock()
    bc.playwright.chromium = MagicMock()
    bc.playwright.chromium.connect_over_cdp = AsyncMock(return_value=fake_browser)

    with patch.object(BrowserController, "_is_cdp_port_alive", return_value=True), \
         patch.object(BrowserController, "_is_edge_running") as is_running, \
         patch.object(BrowserController, "_kill_edge_processes") as kill, \
         patch.object(bc, "_launch_edge_with_debug_port", new=AsyncMock()) as launch:
        await bc._launch_cdp_attach()

    is_running.assert_not_called()
    kill.assert_not_called()
    launch.assert_not_called()
    bc.playwright.chromium.connect_over_cdp.assert_awaited_once()
    assert bc._cdp_attached is True
    assert bc.page is fake_page


@pytest.mark.asyncio
async def test_cdp_attach_uses_automation_profile_when_port_dead():
    """Puerto real cerrado → instancia dedicada de automatización, sin kill."""
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig

    bc = BrowserController(BrowserConfig())

    fake_page = MagicMock(name="page")
    fake_page.is_closed = MagicMock(return_value=False)
    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_browser = MagicMock()
    fake_browser.contexts = []
    fake_browser.new_context = AsyncMock(return_value=fake_context)

    bc.playwright = MagicMock()
    bc.playwright.chromium = MagicMock()
    bc.playwright.chromium.connect_over_cdp = AsyncMock(return_value=fake_browser)

    with patch.object(BrowserController, "_is_cdp_port_alive", return_value=False), \
         patch.object(BrowserController, "_is_edge_running", return_value=False), \
         patch.object(BrowserController, "_kill_edge_processes") as kill, \
         patch.object(bc, "_launch_edge_automation_profile", new=AsyncMock()) as auto_launch, \
         patch.object(bc, "_launch_edge_with_debug_port", new=AsyncMock()) as launch:
        await bc._launch_cdp_attach()

    kill.assert_not_called()
    auto_launch.assert_awaited_once()
    launch.assert_not_called()
    bc.playwright.chromium.connect_over_cdp.assert_awaited_once()
    assert bc._cdp_attached is True


@pytest.mark.asyncio
async def test_cdp_attach_kills_real_edge_only_as_last_fallback():
    """El kill-relaunch del Edge real solo corre si la dedicada falla."""
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig

    bc = BrowserController(BrowserConfig())

    fake_page = MagicMock(name="page")
    fake_page.is_closed = MagicMock(return_value=False)
    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_browser = MagicMock()
    fake_browser.contexts = [fake_context]
    fake_browser.new_context = AsyncMock(return_value=fake_context)

    bc.playwright = MagicMock()
    bc.playwright.chromium = MagicMock()
    bc.playwright.chromium.connect_over_cdp = AsyncMock(return_value=fake_browser)

    with patch.object(BrowserController, "_is_cdp_port_alive", return_value=False), \
         patch.object(BrowserController, "_is_edge_running", return_value=True), \
         patch.object(BrowserController, "_kill_edge_processes") as kill, \
         patch.object(bc, "_launch_edge_automation_profile",
                      new=AsyncMock(side_effect=RuntimeError("dedicada falló"))), \
         patch.object(bc, "_wait_for_edge_exit", new=AsyncMock(return_value=True)), \
         patch.object(bc, "_launch_edge_with_debug_port", new=AsyncMock()) as launch, \
         patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        await bc._launch_cdp_attach()

    kill.assert_called_once()
    launch.assert_awaited_once()
    bc.playwright.chromium.connect_over_cdp.assert_awaited_once()


# -------------------------------------------------------------------
# 4. _launch_edge_with_debug_port pasa los args correctos
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_launch_edge_uses_real_profile_and_debug_port():
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig

    cfg = BrowserConfig()
    bc = BrowserController(cfg)

    captured_args = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured_args["args"] = args
            captured_args["kwargs"] = kwargs

    # Tras crear Popen, simulamos que el puerto está disponible
    with patch("modules.automation.browser_controller.subprocess.Popen", FakePopen), \
         patch.object(BrowserController, "_is_cdp_port_alive", return_value=True):
        await bc._launch_edge_with_debug_port()

    args = captured_args["args"]
    assert args[0] == cfg.edge_executable
    assert any(a.startswith("--remote-debugging-port=") for a in args)
    user_data_arg = next(a for a in args if a.startswith("--user-data-dir="))
    assert user_data_arg == f"--user-data-dir={cfg.user_profile_path}"
    # Confirma que es el perfil real, no clon
    assert "clone" not in user_data_arg.lower()


# -------------------------------------------------------------------
# 5. _close_browser_internal NO cierra contexts del usuario en modo CDP
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_in_cdp_mode_does_not_close_user_context():
    """En modo CDP solo cerramos nuestra pestaña + desconectamos. NO context.close()."""
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig

    bc = BrowserController(BrowserConfig())
    bc._cdp_attached = True

    fake_context = MagicMock()
    fake_context.close = AsyncMock()
    fake_page = MagicMock()
    fake_page.close = AsyncMock()
    fake_browser = MagicMock()
    fake_browser.close = AsyncMock()
    fake_pw = MagicMock()
    fake_pw.stop = AsyncMock()

    bc.context = fake_context
    bc.page = fake_page
    bc.browser = fake_browser
    bc.playwright = fake_pw

    await bc._close_browser_internal()

    fake_context.close.assert_not_called()  # CRÍTICO: no tocamos las tabs del usuario
    fake_page.close.assert_awaited_once()
    fake_browser.close.assert_awaited_once()  # En CDP, close = disconnect
    fake_pw.stop.assert_awaited_once()
    assert bc._cdp_attached is False
