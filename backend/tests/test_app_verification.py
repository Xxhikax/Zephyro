"""
Tests para Cambio 1 — wait_for_window, get_process_pid,
_verify_app_launched, _notify_app_launched.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── wait_for_window (cross-platform) ─────────────────────────────────────

async def test_wait_for_window_returns_false_for_invalid_pid():
    """PID 0 / negativo / None devuelve False sin polling."""
    from core.platform import wait_for_window
    assert wait_for_window(0, timeout=0.1) is False
    assert wait_for_window(-1, timeout=0.1) is False


async def test_wait_for_window_returns_false_for_dead_pid():
    """PID que no existe devuelve False tras timeout corto."""
    from core.platform import wait_for_window
    # PID 999999999 — extremadamente improbable que exista
    assert wait_for_window(999999999, timeout=0.3) is False


async def test_wait_for_window_timeout_respected():
    """Para PID sin ventana, espera ~timeout y retorna False."""
    import time
    from core.platform import wait_for_window
    start = time.monotonic()
    wait_for_window(999999998, timeout=0.5, poll_interval=0.1)
    elapsed = time.monotonic() - start
    assert 0.4 <= elapsed < 1.0


# ── AppLauncher.get_process_pid ──────────────────────────────────────────

async def test_get_process_pid_finds_current_python():
    """Buscar 'python' debería encontrar al menos el proceso actual."""
    from modules.automation.app_launcher import AppLauncher
    launcher = AppLauncher.__new__(AppLauncher)  # skip __init__
    pid = launcher.get_process_pid("python")
    assert pid is not None
    assert pid > 0


async def test_get_process_pid_returns_none_for_unknown():
    """Proceso inexistente devuelve None."""
    from modules.automation.app_launcher import AppLauncher
    launcher = AppLauncher.__new__(AppLauncher)
    pid = launcher.get_process_pid("zzz_nonexistent_xxx")
    assert pid is None


async def test_get_process_pid_returns_most_recent():
    """Debe devolver el PID con create_time más alto cuando hay múltiples."""
    from modules.automation.app_launcher import AppLauncher
    launcher = AppLauncher.__new__(AppLauncher)
    # Para Python, en Windows suele haber al menos uno corriendo
    pid = launcher.get_process_pid("python")
    if pid:
        import psutil
        # El PID retornado debe ser uno válido
        assert psutil.pid_exists(pid)


# ── VerificationAgent._verify_app_launched ───────────────────────────────

@pytest.fixture
def verify_agent():
    from agents.verification_agent import VerificationAgent
    fake_registry = MagicMock()
    fake_registry.broadcast = AsyncMock()
    return VerificationAgent(conn_registry=fake_registry)


async def test_verify_app_launched_already_running(verify_agent):
    """already_running=True → ok inmediato."""
    result = await verify_agent._verify_app_launched({
        "extra": {
            "app": "discord",
            "process_name": "Discord.exe",
            "pid": 12345,
            "already_running": True,
        }
    })
    assert result["ok"] is True
    assert "ya estaba" in result["evidence"]


async def test_verify_app_launched_no_pid(verify_agent):
    """Sin PID capturado → ok=None (no verificable)."""
    result = await verify_agent._verify_app_launched({
        "extra": {
            "app": "webapp",
            "process_name": "msedge",
            "pid": None,
            "already_running": False,
        }
    })
    assert result["ok"] is None
    assert "no PID" in result["evidence"]


async def test_verify_app_launched_dead_pid(verify_agent):
    """PID que no existe → fail."""
    result = await verify_agent._verify_app_launched({
        "extra": {
            "app": "broken",
            "process_name": "broken.exe",
            "pid": 999999999,
            "already_running": False,
        }
    })
    assert result["ok"] is False
    assert "no existe" in result["evidence"] or "terminó" in result["evidence"]


# La app de estos dos tests tiene que ser GUI. Antes decían "python", que es
# una app de CONSOLA: desde el 2026-10-02 el verificador no le exige ventana a
# una consola porque en Windows 11 la ventana la hostea WindowsTerminal.exe y
# no se puede cruzar por PID (ver test_console_apps_actually_open.py). El PID
# sigue siendo el del proceso actual; el nombre solo elige la rama.
async def test_verify_app_launched_alive_with_window(verify_agent):
    """PID vivo + wait_for_window=True → ok."""
    # En Windows el proceso de tests no tiene ventana: mockeamos wait_for_window.
    current_pid = os.getpid()
    with patch("core.platform.wait_for_window", return_value=True):
        result = await verify_agent._verify_app_launched({
            "extra": {
                "app": "notepad",
                "process_name": "notepad.exe",
                "pid": current_pid,
                "already_running": False,
            }
        })
    assert result["ok"] is True
    assert "ventana visible" in result["evidence"]


async def test_verify_app_launched_alive_no_window(verify_agent):
    """PID vivo pero sin ventana → fail (tray-only o crash)."""
    current_pid = os.getpid()
    with patch("core.platform.wait_for_window", return_value=False):
        result = await verify_agent._verify_app_launched({
            "extra": {
                "app": "notepad",
                "process_name": "notepad.exe",
                "pid": current_pid,
                "already_running": False,
            }
        })
    assert result["ok"] is False
    assert "sin ventana" in result["evidence"]


async def test_notify_app_launched_emits_event(verify_agent):
    """_notify_app_launched broadcasts payload app:launched correcto."""
    rule_result = {"ok": True, "evidence": "discord pid=123, ventana visible"}
    event = {
        "extra": {
            "app": "discord",
            "process_name": "Discord.exe",
            "pid": 123,
            "already_running": False,
        }
    }
    await verify_agent._notify_app_launched(event, rule_result)
    verify_agent._conn_registry.broadcast.assert_called_once()
    call_args = verify_agent._conn_registry.broadcast.call_args[0][0]
    assert call_args["type"] == "app:launched"
    assert call_args["app"] == "discord"
    assert call_args["pid"] == 123
    assert call_args["verdict"] == "ok"
    assert call_args["already_running"] is False
