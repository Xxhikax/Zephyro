"""
Tests para el perfil dedicado de automatización y la recuperación CDP segura.

Verifica:
1. BrowserConfig expone automation_profile_path y automation_cdp_port
   (puerto separado del Edge real).
2. _get_active_edge_profile lee Local State y cae a "Default" ante errores.
3. _launch_cdp_attach usa la instancia dedicada sin matar el Edge real,
   reutiliza una instancia dedicada ya corriendo, y solo cae al
   kill-relaunch del Edge real cuando la dedicada falla Y el llamador
   lo permite (allow_real_edge_relaunch=True).
4. connect_over_cdp reintenta 3 veces antes de propagar la excepción.
5. _wait_for_edge_exit polea la salida real de los procesos.
6. El health monitor (trigger_fallback) nunca habilita el relaunch del
   Edge real y restaura READY tras una recuperación exitosa.
7. El flag de first-run del perfil dedicado se setea al crear el
   directorio y AppController lo convierte en una nota al usuario.

Todos los tests son mockeados: NUNCA lanzan ni matan un browser real.
"""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _make_controller():
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig
    return BrowserController(BrowserConfig())


def _wire_fake_playwright(bc, contexts=1):
    """Conecta mocks de playwright/browser/context/page al controller."""
    fake_page = MagicMock(name="page")
    fake_page.is_closed = MagicMock(return_value=False)
    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_browser = MagicMock()
    fake_browser.contexts = [fake_context] * contexts
    fake_browser.new_context = AsyncMock(return_value=fake_context)

    bc.playwright = MagicMock()
    bc.playwright.chromium = MagicMock()
    bc.playwright.chromium.connect_over_cdp = AsyncMock(return_value=fake_browser)
    return fake_browser, fake_context, fake_page


# -------------------------------------------------------------------
# 1. BrowserConfig: campos del perfil dedicado
# -------------------------------------------------------------------

def test_config_has_automation_profile_defaults():
    from core.browser_config import BrowserConfig

    cfg = BrowserConfig()
    assert cfg.automation_profile_path, "automation_profile_path debe resolverse"
    assert "Zephyro" in cfg.automation_profile_path
    assert isinstance(cfg.automation_cdp_port, int)
    assert cfg.automation_cdp_port != cfg.cdp_port, (
        "El puerto de automatización debe ser distinto al del Edge real"
    )


# -------------------------------------------------------------------
# 2. _get_active_edge_profile
# -------------------------------------------------------------------

def test_get_active_edge_profile_reads_local_state(tmp_path):
    from core.browser_config import _get_active_edge_profile

    local_state = tmp_path / "Local State"
    local_state.write_text(
        json.dumps({"profile": {"last_used": "Profile 3"}}), encoding="utf-8"
    )
    assert _get_active_edge_profile(str(tmp_path)) == "Profile 3"


def test_get_active_edge_profile_fallback_default(tmp_path):
    from core.browser_config import _get_active_edge_profile

    # Sin directorio
    assert _get_active_edge_profile(None) == "Default"
    # Directorio sin Local State
    assert _get_active_edge_profile(str(tmp_path)) == "Default"
    # Local State corrupto
    (tmp_path / "Local State").write_text("{not json", encoding="utf-8")
    assert _get_active_edge_profile(str(tmp_path)) == "Default"
    # Local State sin last_used
    (tmp_path / "Local State").write_text(json.dumps({"profile": {}}), encoding="utf-8")
    assert _get_active_edge_profile(str(tmp_path)) == "Default"


# -------------------------------------------------------------------
# 3. _launch_cdp_attach: orden de attach
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attach_uses_automation_profile_without_killing_real_edge():
    """Puerto real muerto → instancia dedicada. El Edge real no se toca."""
    from modules.automation.browser_controller import BrowserController

    bc = _make_controller()
    _wire_fake_playwright(bc)

    with patch.object(BrowserController, "_is_cdp_port_alive", return_value=False), \
         patch.object(BrowserController, "_is_edge_running") as is_running, \
         patch.object(BrowserController, "_kill_edge_processes") as kill, \
         patch.object(bc, "_launch_edge_automation_profile", new=AsyncMock()) as auto_launch, \
         patch.object(bc, "_launch_edge_with_debug_port", new=AsyncMock()) as real_launch:
        await bc._launch_cdp_attach()

    auto_launch.assert_awaited_once()
    real_launch.assert_not_called()
    kill.assert_not_called()
    is_running.assert_not_called()
    # Se conecta al puerto de automatización, no al del Edge real
    cdp_url = bc.playwright.chromium.connect_over_cdp.await_args.args[0]
    assert str(bc.config.automation_cdp_port) in cdp_url
    assert bc._cdp_attached is True


@pytest.mark.asyncio
async def test_attach_reuses_running_automation_instance():
    """Si la instancia dedicada ya corre, attach directo sin relanzar nada."""
    from modules.automation.browser_controller import BrowserController

    bc = _make_controller()
    _wire_fake_playwright(bc)

    # Primer check (puerto real) False, segundo (puerto automatización) True
    with patch.object(BrowserController, "_is_cdp_port_alive", side_effect=[False, True]), \
         patch.object(bc, "_launch_edge_automation_profile", new=AsyncMock()) as auto_launch, \
         patch.object(bc, "_launch_edge_with_debug_port", new=AsyncMock()) as real_launch, \
         patch.object(BrowserController, "_kill_edge_processes") as kill:
        await bc._launch_cdp_attach()

    auto_launch.assert_not_called()
    real_launch.assert_not_called()
    kill.assert_not_called()
    cdp_url = bc.playwright.chromium.connect_over_cdp.await_args.args[0]
    assert str(bc.config.automation_cdp_port) in cdp_url


@pytest.mark.asyncio
async def test_attach_fallback_to_real_edge_when_automation_fails():
    """Instancia dedicada falla → kill graceful + relaunch del Edge real."""
    from modules.automation.browser_controller import BrowserController

    bc = _make_controller()
    _wire_fake_playwright(bc)

    with patch.object(BrowserController, "_is_cdp_port_alive", return_value=False), \
         patch.object(BrowserController, "_is_edge_running", return_value=True), \
         patch.object(BrowserController, "_kill_edge_processes") as kill, \
         patch.object(bc, "_launch_edge_automation_profile",
                      new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(bc, "_wait_for_edge_exit", new=AsyncMock(return_value=True)), \
         patch.object(bc, "_launch_edge_with_debug_port", new=AsyncMock()) as real_launch:
        await bc._launch_cdp_attach()

    kill.assert_called_once_with(False)  # solo cierre graceful, sin force
    real_launch.assert_awaited_once()
    cdp_url = bc.playwright.chromium.connect_over_cdp.await_args.args[0]
    assert str(bc.config.cdp_port) in cdp_url


@pytest.mark.asyncio
async def test_attach_no_real_edge_relaunch_when_disallowed():
    """allow_real_edge_relaunch=False: si la dedicada falla, NO se mata Edge."""
    from modules.automation.browser_controller import BrowserController

    bc = _make_controller()
    _wire_fake_playwright(bc)

    with patch.object(BrowserController, "_is_cdp_port_alive", return_value=False), \
         patch.object(BrowserController, "_kill_edge_processes") as kill, \
         patch.object(bc, "_launch_edge_automation_profile",
                      new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(bc, "_launch_edge_with_debug_port", new=AsyncMock()) as real_launch:
        with pytest.raises(RuntimeError):
            await bc._launch_cdp_attach(allow_real_edge_relaunch=False)

    kill.assert_not_called()
    real_launch.assert_not_called()


# -------------------------------------------------------------------
# 4. Retry de connect_over_cdp
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_over_cdp_retries_then_succeeds():
    from modules.automation.browser_controller import BrowserController

    bc = _make_controller()
    fake_browser, _, _ = _wire_fake_playwright(bc)
    bc.playwright.chromium.connect_over_cdp = AsyncMock(
        side_effect=[TimeoutError(), TimeoutError(), fake_browser]
    )

    with patch.object(BrowserController, "_is_cdp_port_alive", return_value=True), \
         patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        await bc._launch_cdp_attach()

    assert bc.playwright.chromium.connect_over_cdp.await_count == 3
    assert bc._cdp_attached is True


@pytest.mark.asyncio
async def test_connect_over_cdp_raises_after_three_failures():
    from modules.automation.browser_controller import BrowserController

    bc = _make_controller()
    _wire_fake_playwright(bc)
    bc.playwright.chromium.connect_over_cdp = AsyncMock(side_effect=TimeoutError())

    with patch.object(BrowserController, "_is_cdp_port_alive", return_value=True), \
         patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(TimeoutError):
            await bc._launch_cdp_attach()

    assert bc.playwright.chromium.connect_over_cdp.await_count == 3


# -------------------------------------------------------------------
# 5. _wait_for_edge_exit
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wait_for_edge_exit_returns_true_when_processes_exit():
    from modules.automation.browser_controller import BrowserController

    with patch.object(BrowserController, "_is_edge_running", side_effect=[True, False]):
        assert await BrowserController._wait_for_edge_exit(timeout=5.0, poll_interval=0.01)


@pytest.mark.asyncio
async def test_wait_for_edge_exit_returns_false_on_timeout():
    from modules.automation.browser_controller import BrowserController

    with patch.object(BrowserController, "_is_edge_running", return_value=True):
        assert not await BrowserController._wait_for_edge_exit(timeout=0.05, poll_interval=0.01)


# -------------------------------------------------------------------
# 6. Health monitor: trigger_fallback seguro
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_fallback_restores_ready_and_disallows_real_relaunch():
    from modules.automation.browser_controller import BrowserController, BrowserState

    bc = _make_controller()
    bc._cdp_attached = True
    bc._close_browser_internal = AsyncMock()
    bc._launch_cdp_attach = AsyncMock()
    bc._set_state = AsyncMock()
    bc._set_health = AsyncMock()

    monitor = BrowserController._HealthMonitor(bc)
    result = await monitor.trigger_fallback("test_reason")

    assert result is True
    bc._launch_cdp_attach.assert_awaited_once_with(allow_real_edge_relaunch=False)
    state_arg = bc._set_state.await_args.args[0]
    assert state_arg == BrowserState.READY


@pytest.mark.asyncio
async def test_trigger_fallback_sets_error_without_touching_real_edge():
    from modules.automation.browser_controller import BrowserController, BrowserState

    bc = _make_controller()
    bc._cdp_attached = True
    bc._close_browser_internal = AsyncMock()
    bc._launch_cdp_attach = AsyncMock(side_effect=TimeoutError())
    bc._set_state = AsyncMock()
    bc._set_health = AsyncMock()

    monitor = BrowserController._HealthMonitor(bc)
    with patch.object(BrowserController, "_kill_edge_processes") as kill:
        result = await monitor.trigger_fallback("test_reason")

    assert result is False
    kill.assert_not_called()
    state_arg = bc._set_state.await_args.args[0]
    assert state_arg == BrowserState.ERROR


# -------------------------------------------------------------------
# 7. Perfil dedicado: first-run flag y args de lanzamiento
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_automation_profile_first_run_flag(tmp_path):
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig

    cfg = BrowserConfig(automation_profile_path=str(tmp_path / "EdgeAutomation"))
    bc = BrowserController(cfg)

    captured = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args

    with patch("modules.automation.browser_controller.subprocess.Popen", FakePopen), \
         patch.object(BrowserController, "_is_cdp_port_alive", return_value=True):
        await bc._launch_edge_automation_profile()

    assert bc._automation_profile_first_run is True
    assert (tmp_path / "EdgeAutomation").exists()
    args = captured["args"]
    assert any(a == f"--remote-debugging-port={cfg.automation_cdp_port}" for a in args)
    user_data_arg = next(a for a in args if a.startswith("--user-data-dir="))
    assert "EdgeAutomation" in user_data_arg

    # Segundo lanzamiento: el directorio ya existe → no es first-run
    bc._automation_profile_first_run = False
    with patch("modules.automation.browser_controller.subprocess.Popen", FakePopen), \
         patch.object(BrowserController, "_is_cdp_port_alive", return_value=True):
        await bc._launch_edge_automation_profile()
    assert bc._automation_profile_first_run is False


@pytest.mark.asyncio
async def test_launch_edge_with_debug_port_includes_profile_directory():
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig

    cfg = BrowserConfig()
    bc = BrowserController(cfg)

    captured = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args

    with patch("modules.automation.browser_controller.subprocess.Popen", FakePopen), \
         patch("modules.automation.browser_controller._get_active_edge_profile",
               return_value="Profile 7"), \
         patch.object(BrowserController, "_is_cdp_port_alive", return_value=True):
        await bc._launch_edge_with_debug_port()

    assert "--profile-directory=Profile 7" in captured["args"]


@pytest.mark.asyncio
async def test_app_controller_prepends_first_run_note():
    from modules.automation.app_controller import AppController

    ac = AppController.__new__(AppController)  # sin __init__: no toca daemons
    ac._browser = MagicMock(_automation_profile_first_run=True)
    ac._execute_inner = AsyncMock(return_value="Listo.")

    result = await ac.execute({"automation_intent": "NAVIGATE"})

    assert result.startswith("Nota: se creó un perfil de Edge dedicado")
    assert result.endswith("Listo.")
    assert ac._browser._automation_profile_first_run is False
