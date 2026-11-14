"""Regressions from the 2026-10 code review of the working tree.

Each test pins one confirmed defect:
  1. Focus monitor closed EVERY tab when the distraction had no URL.
  4. Stopping study mode mid-Pomodoro closed the Pomodoro's work interval.
  5. A stale foreground game exe kept classifying other windows as "game".
  7. "abre <phrase> de <site>" turned arbitrary phrases into URL sub-paths.
 10. Few-shot tripped the global Ollama breaker on faiss/numpy errors.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from modules.schedule.monitor import FocusMonitor


def _monitor_with_browser() -> tuple[FocusMonitor, AsyncMock]:
    m = FocusMonitor()
    close = AsyncMock(return_value="1 pestaña(s) cerrada(s) (youtube.com)")
    m._app_controller = MagicMock(_browser=MagicMock(close_distraction_tabs=close))
    return m, close


# ── 1. Empty URL must never reach close_distraction_tabs ─────────────────────

@pytest.mark.parametrize("url", ["", "   "])
def test_enforce_without_url_closes_nothing(url):
    m, close = _monitor_with_browser()
    assert asyncio.run(m._enforce_browser_close(url)) is None
    close.assert_not_called()


def test_enforce_with_url_targets_its_domain():
    m, close = _monitor_with_browser()
    asyncio.run(m._enforce_browser_close("https://www.youtube.com/watch?v=x"))
    close.assert_awaited_once_with("youtube.com")


def test_browser_controller_refuses_empty_pattern():
    from modules.automation.browser_controller import BrowserController

    bc = BrowserController.__new__(BrowserController)
    page = MagicMock(url="https://github.com/me")
    page.close = AsyncMock()
    bc.context = MagicMock(pages=[page])
    out = asyncio.run(bc._close_distraction_tabs_impl(""))
    assert out.startswith("0 ")
    page.close.assert_not_called()


# ── 4. Study mode stop must not end a running Pomodoro's work interval ───────

def test_stop_study_mode_keeps_pomodoro_work_interval_open():
    async def scenario():
        m = FocusMonitor()
        m._pomodoro_task = asyncio.create_task(asyncio.sleep(60))
        m._study_mode_active = True
        m._open_work_interval()
        await m.stop_study_mode()
        still_open = m._work_intervals[-1][1] is None
        m._pomodoro_task.cancel()
        return still_open

    assert asyncio.run(scenario()) is True


def test_stop_study_mode_alone_closes_work_interval():
    async def scenario():
        m = FocusMonitor()
        m._study_mode_active = True
        m._open_work_interval()
        await m.stop_study_mode()
        return m._work_intervals[-1][1]

    assert asyncio.run(scenario()) is not None


# ── 5. Foreground owner refreshes every tick, not every full scan ────────────

def test_foreground_refresh_clears_stale_game(monkeypatch):
    import core.platform as platform_mod

    m = FocusMonitor()
    m._foreground_proc = next(iter(__import__(
        "modules.schedule.monitor", fromlist=["_GAME_PROCESSES"])._GAME_PROCESSES))
    assert m._classify_distraction("main.py - Visual Studio Code", "")[1] == "game"

    monkeypatch.setattr(platform_mod, "foreground_process_name", lambda: "code.exe")
    asyncio.run(m._refresh_foreground_proc())
    assert m._classify_distraction("main.py - Visual Studio Code", "") == (False, "")


def test_start_study_mode_resets_foreground():
    async def scenario():
        m = FocusMonitor()
        m._foreground_proc = "steam.exe"
        m._start_detection_loop = AsyncMock()
        await m.start_study_mode()
        return m._foreground_proc

    assert asyncio.run(scenario()) == ""


# ── 7. Open-verb nav-subpath only for bare one-word sections ─────────────────

@pytest.fixture(scope="module")
def router():
    from tests.test_automation_routing import make_router
    return make_router()


@pytest.mark.parametrize("text", [
    "abre el perfil de instagram",
    "abre un video de youtube",
    "abre mis mensajes de whatsapp",
])
def test_open_verb_phrase_is_not_a_subpath(router, text):
    r = asyncio.run(router.classify_and_parse(text))
    assert r.get("_source") != "capa1_nav_subpath", r
    assert not r.get("sub_path"), r


@pytest.mark.parametrize("text,sub", [
    ("abre reels de instagram", "reels"),
    ("abrime reels de instagram", "reels"),
    ("entra a reels de instagram", "reels"),
])
def test_named_section_still_routes(router, text, sub):
    r = asyncio.run(router.classify_and_parse(text))
    assert r.get("target") == "instagram" and r.get("sub_path") == sub, r


# ── 10. Only embedding failures implicate Ollama ─────────────────────────────

def _selector(embed):
    from modules.learning.few_shot_selector import DynamicFewShotSelector

    sel = DynamicFewShotSelector.__new__(DynamicFewShotSelector)
    store = MagicMock()
    store.lock = MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False)
    store.vectors = [[1.0, 0.0, 0.0]]
    store.documents = ["d"]
    store.metadatas = [{"user_input": "u", "response": "r"}]
    store.embedding_function.embed_query = embed
    sel._store = store
    sel._available = True
    return sel


def _patch_breaker(monkeypatch):
    import core.ollama_health as oh
    down = MagicMock()
    monkeypatch.setattr(oh, "is_ollama_up", lambda: True)
    monkeypatch.setattr(oh, "mark_ollama_down", down)
    return down


def test_index_error_does_not_trip_ollama_breaker(monkeypatch):
    down = _patch_breaker(monkeypatch)
    sel = _selector(lambda q: [1.0, 0.0])  # dimension mismatch vs stored vectors
    assert sel.get_examples("una consulta cualquiera") == []
    down.assert_not_called()


def test_embedding_error_trips_ollama_breaker(monkeypatch):
    down = _patch_breaker(monkeypatch)

    def boom(q):
        raise ConnectionError("ollama down")

    sel = _selector(boom)
    assert sel.get_examples("una consulta cualquiera") == []
    down.assert_called_once()
