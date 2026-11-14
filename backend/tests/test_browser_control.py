"""
Tests para Cambio 3 — BrowserController.kill/restart/tail_logs +
BrowserLogWatcher + browser router.
"""
import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── BrowserController public control methods ────────────────────────────────

@pytest.fixture
def fake_browser_controller():
    """BrowserController con mínimas dependencias mockeadas para testear
    los métodos públicos sin lanzar Playwright real."""
    from modules.automation.browser_controller import BrowserController, BrowserState, BrowserHealth
    from core.browser_config import BrowserConfig

    config = BrowserConfig()
    # Apuntar log_file a un tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log")
    tmp.write("line one\nline two\nline three\n")
    tmp.close()
    config.log_file = tmp.name

    bc = BrowserController(config=config)
    # Marcar como CLOSED para evitar lazy-init
    bc._state = BrowserState.CLOSED
    bc._health = BrowserHealth.UNKNOWN
    bc._command_queue = None
    bc._browser = None
    bc.playwright = None
    bc.browser = None
    bc.context = None
    bc.page = None
    yield bc
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


async def test_kill_no_browser_running(fake_browser_controller):
    """kill() funciona aunque no haya browser activo."""
    result = await fake_browser_controller.kill()
    assert result["success"] is True
    assert "drained" in result


async def test_tail_logs_reads_last_n_lines(fake_browser_controller):
    """tail_logs(N) devuelve las últimas N líneas del archivo."""
    lines = fake_browser_controller.tail_logs(n=2)
    assert lines == ["line two", "line three"]


async def test_tail_logs_handles_missing_file(fake_browser_controller):
    """Si el archivo no existe, devuelve [] sin lanzar."""
    fake_browser_controller.config.log_file = "/nonexistent/path.log"
    lines = fake_browser_controller.tail_logs(n=10)
    assert lines == []


async def test_tail_logs_clamps_to_file_size(fake_browser_controller):
    """Si N > líneas totales, devuelve todas."""
    lines = fake_browser_controller.tail_logs(n=999)
    assert len(lines) == 3


# ── BrowserLogWatcher subscribe/unsubscribe ─────────────────────────────────

async def test_browser_log_watcher_subscribe_unsubscribe():
    """subscribe agrega user_id, unsubscribe lo quita."""
    from core.browser_log_watcher import BrowserLogWatcher

    w = BrowserLogWatcher("nonexistent.log")
    assert not w.has_subscribers()
    w.subscribe("user1")
    assert w.has_subscribers()
    assert "user1" in w._subscribers
    w.subscribe("user2")
    assert len(w._subscribers) == 2
    w.unsubscribe("user1")
    assert "user1" not in w._subscribers
    assert "user2" in w._subscribers


async def test_browser_log_watcher_idles_without_subscribers():
    """run() sleep largo cuando no hay subscribers — no debe poll el archivo."""
    from core.browser_log_watcher import BrowserLogWatcher

    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log")
    tmp.write("initial\n")
    tmp.close()

    w = BrowserLogWatcher(tmp.name)
    await w.start()
    await asyncio.sleep(0.1)
    # Sin subscribers, no debió leer
    initial_pos = w._position
    await asyncio.sleep(0.1)
    assert w._position == initial_pos
    await w.stop()
    os.unlink(tmp.name)


async def test_browser_log_watcher_emits_on_new_lines():
    """Cuando un subscriber está activo, líneas nuevas se emiten."""
    from core.browser_log_watcher import BrowserLogWatcher

    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log")
    tmp.write("initial line\n")
    tmp.close()

    w = BrowserLogWatcher(tmp.name)
    pushed: list[dict] = []

    # Mock registry
    async def fake_push(uid, payload):
        pushed.append(payload)
        return True

    with patch("core.browser_log_watcher.registry") as mock_registry:
        mock_registry.push = AsyncMock(side_effect=fake_push)
        await w.start()
        w.subscribe("user1")
        await asyncio.sleep(0.6)  # deja que arranque el polling
        # Agregar líneas nuevas
        with open(tmp.name, "a", encoding="utf-8") as f:
            f.write("new line A\nnew line B\n")
            f.flush()
        # Esperar al menos un poll_interval (0.5s) + slack
        await asyncio.sleep(1.0)
        await w.stop()

    os.unlink(tmp.name)
    payloads = [p for p in pushed if p.get("type") == "browser:log"]
    assert len(payloads) >= 2
    assert any("new line A" in p.get("line", "") for p in payloads)
    assert any("new line B" in p.get("line", "") for p in payloads)
