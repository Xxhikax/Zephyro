"""
Tests para Cambio 2 — ContextDetector + SessionWorkspace.set_active
+ flujo de override manual.
"""
import asyncio
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── SessionWorkspace.set_active / get_active ─────────────────────────────

def test_session_workspace_active_persistence():
    """set_active persiste en SQLite; get_active recupera."""
    from modules.memory_extended.session_workspace import SessionWorkspace
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        ws = SessionWorkspace(db_path=db_path)
        assert ws.get_active() is None
        ws.set_active("proyecto-x")
        assert ws.get_active() == "proyecto-x"
        # Nueva instancia → debe recuperar
        ws2 = SessionWorkspace(db_path=db_path)
        assert ws2.get_active() == "proyecto-x"
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_session_workspace_set_active_none_clears():
    """set_active(None) limpia el workspace activo."""
    from modules.memory_extended.session_workspace import SessionWorkspace
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        ws = SessionWorkspace(db_path=db_path)
        ws.set_active("foo")
        assert ws.get_active() == "foo"
        ws.set_active(None)
        assert ws.get_active() is None
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ── ContextDetector pure logic (no event loop) ───────────────────────────

def test_context_detector_vscode_title_parsing():
    """Regex VSCode extrae proyecto del título."""
    from core.context_detector import _VSCODE_RE
    title = "main.py - zephyro - Visual Studio Code"
    m = _VSCODE_RE.match(title)
    assert m is not None
    assert m.group("project").strip() == "zephyro"
    assert m.group("file").strip() == "main.py"


def test_context_detector_cursor_title_parsing():
    """Regex Cursor (fork de VSCode) también funciona."""
    from core.context_detector import _CURSOR_RE
    title = "config.json - mi-proyecto - Cursor"
    m = _CURSOR_RE.match(title)
    assert m is not None
    assert m.group("project").strip() == "mi-proyecto"


def test_context_detector_filters_own_windows():
    """Ventanas con "Zephyro" / "jarvis-terminal" / etc. son ignoradas."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()
    assert cd._is_own_window("ZEPHYRO - Terminal") is True
    assert cd._is_own_window("jarvis-terminal v0.1.0") is True
    assert cd._is_own_window("Tauri Window") is True
    assert cd._is_own_window("Claude Code") is True
    assert cd._is_own_window("main.py - zephyro - Visual Studio Code") is False


@pytest.mark.asyncio
async def test_context_detector_build_from_vscode_title():
    """_build_context_from_title detecta VSCode correctamente."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()
    ctx = await cd._build_context_from_title("cli.py - zephyro - Visual Studio Code")
    assert ctx["active_project"] == "zephyro"
    assert ctx["vscode_project"] == "zephyro"
    assert ctx["detected_by"] == "auto"


@pytest.mark.asyncio
async def test_context_detector_build_from_browser_title():
    """Browser title → consulta get_browser_url_fn."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()

    async def fake_url():
        return "https://github.com/Anthropic/anthropic-sdk-python"
    cd._get_browser_url_fn = fake_url

    ctx = await cd._build_context_from_title("GitHub — Mozilla Firefox")
    assert ctx["current_url"] == "https://github.com/Anthropic/anthropic-sdk-python"


@pytest.mark.asyncio
async def test_context_detector_build_from_path_title():
    """Title que parece path Windows → active_path."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()
    ctx = await cd._build_context_from_title("C:\\Users\\user\\Documents")
    assert ctx["active_path"] == "C:\\Users\\user\\Documents"


def test_context_detector_changed_detection():
    """_changed devuelve True solo si cambia campo relevante."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()
    cd._context["active_project"] = "old-proj"
    assert cd._changed({"active_project": "new-proj", "active_window": None,
                        "active_path": None, "current_url": None}) is True
    assert cd._changed({"active_project": "old-proj", "active_window": None,
                        "active_path": None, "current_url": None}) is False


# ── ContextDetector manual override + broadcast ─────────────────────────

@pytest.mark.asyncio
async def test_context_detector_manual_workspace_broadcasts():
    """set_manual_workspace actualiza estado y emite broadcast."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()
    fake_registry = MagicMock()
    fake_registry.broadcast = AsyncMock()
    cd._registry = fake_registry

    result = await cd.set_manual_workspace("nuevo-proyecto", path="C:/dev/x")
    assert result["active_project"] == "nuevo-proyecto"
    assert result["active_path"] == "C:/dev/x"
    assert result["detected_by"] == "manual"
    fake_registry.broadcast.assert_called_once()
    payload = fake_registry.broadcast.call_args[0][0]
    assert payload["type"] == "context:update"
    assert payload["active_project"] == "nuevo-proyecto"


@pytest.mark.asyncio
async def test_context_detector_manual_lock_blocks_auto():
    """Tras set_manual_workspace, _tick() no debe sobrescribir por 10min."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()
    fake_registry = MagicMock()
    fake_registry.broadcast = AsyncMock()
    cd._registry = fake_registry

    await cd.set_manual_workspace("manual-proj")
    # Simular tick: aunque haya ventana detectada, NO debe broadcast nada
    fake_registry.broadcast.reset_mock()
    with patch.object(cd, "_get_active_window_title", return_value="other.py - other-proj - Visual Studio Code"):
        await cd._tick()
    fake_registry.broadcast.assert_not_called()
    assert cd._context["active_project"] == "manual-proj"


@pytest.mark.asyncio
async def test_context_detector_manual_lock_expires():
    """Después de _MANUAL_LOCK_SECONDS, auto-detection vuelve."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()
    fake_registry = MagicMock()
    fake_registry.broadcast = AsyncMock()
    cd._registry = fake_registry

    await cd.set_manual_workspace("manual-proj")
    fake_registry.broadcast.reset_mock()
    # Forzar expiración del lock
    cd._manual_until = time.monotonic() - 1
    with patch.object(cd, "_get_active_window_title", return_value="x.py - auto-proj - Visual Studio Code"):
        await cd._tick()
    fake_registry.broadcast.assert_called_once()
    assert cd._context["active_project"] == "auto-proj"


@pytest.mark.asyncio
async def test_context_detector_ignores_own_windows_in_tick():
    """Si la ventana activa es del propio Zephyro → no actualizar."""
    from core.context_detector import ContextDetector
    cd = ContextDetector()
    fake_registry = MagicMock()
    fake_registry.broadcast = AsyncMock()
    cd._registry = fake_registry
    cd._context["active_project"] = "previous"

    with patch.object(cd, "_get_active_window_title", return_value="ZEPHYRO Terminal"):
        await cd._tick()
    fake_registry.broadcast.assert_not_called()
    assert cd._context["active_project"] == "previous"
