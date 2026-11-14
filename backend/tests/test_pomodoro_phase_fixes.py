"""Regression tests for the Fase 1 Pomodoro stability fixes.

Covers:
  - Bug 2: remaining time uses the CURRENT phase duration (not always work).
  - Bug 3: extending a phase never inflates the work-phase base duration.
  - Bug 1: the START regex has a 'task' group and MODE input does not match START
    (the invariant that keeps _pomodoro_activate from raising 'no such group').
"""
import asyncio
import time
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from modules.schedule.monitor import FocusMonitor


def _make_monitor():
    return FocusMonitor()


class _FakeWsRegistry:
    def __init__(self):
        self.events = []

    async def broadcast(self, payload):
        self.events.append(payload)


async def _make_live_task():
    """A long-running task so monitor.<...> treats the session as active."""
    return asyncio.create_task(asyncio.sleep(60))


# ── Bug 2: remaining time is phase-aware ────────────────────────────────────────

def test_current_phase_duration_sec_per_phase():
    m = _make_monitor()
    m._pomodoro_duration_min = 30
    m._short_break_min = 5
    m._long_break_min = 15
    m._pomodoro_phase = "work"
    assert m._current_phase_duration_sec() == 1800
    m._pomodoro_phase = "short_break"
    assert m._current_phase_duration_sec() == 300
    m._pomodoro_phase = "long_break"
    assert m._current_phase_duration_sec() == 900


@pytest.mark.asyncio
async def test_remaining_during_break_uses_break_duration():
    m = _make_monitor()
    task = await _make_live_task()
    m._pomodoro_task = task
    try:
        m._pomodoro_duration_min = 25  # work base — must NOT leak into break remaining
        m._short_break_min = 5
        m._long_break_min = 15
        m._phase_extend_sec = 0.0
        m._pomodoro_start_ts = time.time()

        m._pomodoro_phase = "short_break"
        assert m._pomodoro_remaining_min() == 5

        m._pomodoro_phase = "long_break"
        assert m._pomodoro_remaining_min() == 15
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_remaining_includes_phase_extend():
    m = _make_monitor()
    task = await _make_live_task()
    m._pomodoro_task = task
    try:
        m._pomodoro_phase = "work"
        m._pomodoro_duration_min = 25
        m._phase_extend_sec = 120  # +2 min added to the current phase
        m._pomodoro_start_ts = time.time()
        assert m._pomodoro_remaining_min() == 27
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_remaining_frozen_while_paused():
    m = _make_monitor()
    task = await _make_live_task()
    m._pomodoro_task = task
    try:
        m._pomodoro_phase = "work"
        m._pomodoro_duration_min = 25  # 1500s total
        m._phase_extend_sec = 0.0
        # Wall: started 10 min ago. Pause began 5 min ago, so only 5 min of work
        # had elapsed when paused (600 - 300 = 300s). Frozen → 1500-300 = 20 min.
        # If the clock were NOT frozen it would read 15 min; expecting 20 proves
        # the pause freeze works (and is unambiguous: both values are exact minutes).
        now = time.time()
        m._pomodoro_start_ts = now - 600
        m._pomodoro_paused = True
        m._pomodoro_pause_started_at = now - 300
        assert m._pomodoro_remaining_min() == 20
    finally:
        task.cancel()


# ── Bug 3: extend never inflates the work base duration ─────────────────────────

@pytest.mark.asyncio
async def test_extend_api_does_not_mutate_work_duration():
    m = _make_monitor()
    task = await _make_live_task()
    m._pomodoro_task = task
    try:
        m._pomodoro_duration_min = 25
        m._pomodoro_extend_pending = 0
        await m.extend_pomodoro(10, mode="current")
        # The API only queues the extend; it must not touch the work base.
        assert m._pomodoro_duration_min == 25
        assert m._pomodoro_extend_pending == 10
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_sleep_loop_extend_lands_on_phase_not_work_duration():
    """The sleep loop must consume _pomodoro_extend_pending into _phase_extend_sec,
    never into _pomodoro_duration_min (which would inflate every future work cycle)."""
    m = _make_monitor()
    m.set_ws_registry(_FakeWsRegistry())
    m._pomodoro_pause_event = asyncio.Event()
    m._pomodoro_pause_event.set()
    m._pomodoro_phase = "short_break"
    m._short_break_min = 1
    m._pomodoro_duration_min = 25
    m._phase_extend_sec = 0.0
    m._pomodoro_start_ts = time.time()
    m._pomodoro_extend_pending = 2  # +2 min queued before the loop runs

    async def _stopper():
        for _ in range(300):
            await asyncio.sleep(0.02)
            if m._pomodoro_extend_pending == 0 and m._phase_extend_sec > 0:
                m._pomodoro_skip_current = True
                return
        m._pomodoro_skip_current = True

    asyncio.create_task(_stopper())
    await asyncio.wait_for(m._cancellable_sleep(0.1), timeout=8)

    assert m._pomodoro_duration_min == 25      # work base untouched (Bug 3)
    assert m._phase_extend_sec == 120          # extend landed on the current phase


# ── Bug 1: structural invariant behind 'no such group' ──────────────────────────

def test_start_re_has_task_group_and_mode_input_does_not_match_start():
    from skills.builtin.schedule.skill import _POMODORO_START_RE, _POMODORO_MODE_RE
    # _pomodoro_activate calls m.group("task"); the START pattern MUST define it.
    assert "task" in _POMODORO_START_RE.groupindex
    # "modo pomodoro" is handled by MODE (no groups) and must NOT match START,
    # so activate re-derives m from START and guards with `m and ...`.
    assert _POMODORO_MODE_RE.search("modo pomodoro") is not None
    assert _POMODORO_START_RE.search("modo pomodoro") is None
