"""Tests para multi-stage logic en ProactiveNotifier."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestReminderProfiles:
    def test_profiles_defined(self):
        from core.proactive_notifier import REMINDER_PROFILES
        assert "default" in REMINDER_PROFILES
        assert "fitness" in REMINDER_PROFILES

    def test_fitness_profile_has_multi_stage(self):
        from core.proactive_notifier import REMINDER_PROFILES
        f = REMINDER_PROFILES["fitness"]
        assert 60 in f["pre_minutes"]
        assert 15 in f["pre_minutes"]
        assert f["at_start"] is True
        assert f["missed_minutes"] == 30

    def test_default_profile_is_backward_compatible(self):
        from core.proactive_notifier import REMINDER_PROFILES
        d = REMINDER_PROFILES["default"]
        assert d["pre_minutes"] == [10]
        assert d["at_start"] is False
        assert d["missed_minutes"] is None


def _make_notifier(schedule_service=None):
    from core.notification_center import NotificationCenter
    from core.notification_rules import RuleStore
    from core.proactive_notifier import ProactiveNotifier
    if schedule_service is None:
        schedule_service = MagicMock()
    ws = MagicMock()
    ws.broadcast = AsyncMock()
    # Center con store temporal: sin esto los tests escribirían en la DB de
    # reglas real (storage/notifications.db) vía el singleton.
    tmp_db = Path(tempfile.mkdtemp(prefix="zephyro-notif-")) / "notifications.db"
    center = NotificationCenter(store=RuleStore(db_path=str(tmp_db)))
    return ProactiveNotifier(
        schedule_service=schedule_service,
        reminder_system=None,
        channel_manager=None,
        ws_registry=ws,
        user_id="admin",
        notification_center=center,
    )


def _ev(id, start_ts, profile="fitness", status="pending"):
    return SimpleNamespace(
        id=id, title=f"E{id}", category="fitness", start_ts_utc=start_ts,
        end_ts_utc=start_ts + 3600, notes="", status=status,
        reminder_profile=profile,
    )


class TestDispatchStages:
    @pytest.mark.asyncio
    async def test_fires_pre_60_within_window(self):
        n = _make_notifier()
        now = time.time()
        ev = _ev(1, now + 60 * 60)  # exactamente T-60
        await n._dispatch_stages(ev, now)
        assert "event_1_pre_60" in n._notified

    @pytest.mark.asyncio
    async def test_does_not_refire_same_stage(self):
        n = _make_notifier()
        now = time.time()
        ev = _ev(2, now + 60 * 60)
        await n._dispatch_stages(ev, now)
        n.ws_registry.broadcast.reset_mock()
        # llamarlo de nuevo en el mismo segundo
        await n._dispatch_stages(ev, now)
        assert n.ws_registry.broadcast.call_count == 0

    @pytest.mark.asyncio
    async def test_default_profile_only_pre_10(self):
        n = _make_notifier()
        now = time.time()
        ev = _ev(3, now + 10 * 60, profile=None)  # None == default
        await n._dispatch_stages(ev, now)
        assert "event_3_pre_10" in n._notified
        assert "event_3_pre_60" not in n._notified

    @pytest.mark.asyncio
    async def test_at_start_fires_when_now_equals_start(self):
        n = _make_notifier()
        now = time.time()
        ev = _ev(4, now)  # justo arrancando
        await n._dispatch_stages(ev, now)
        assert "event_4_at_start" in n._notified

    @pytest.mark.asyncio
    async def test_missed_marks_event_and_fires(self):
        sched = MagicMock()
        sched.get_event_by_id = MagicMock(return_value=_ev(5, time.time() - 1800, status="pending"))
        sched.mark_missed = MagicMock(return_value=True)
        n = _make_notifier(schedule_service=sched)
        now = time.time()
        ev = _ev(5, now - 30 * 60)  # start fue hace 30 min → missed_ts=now
        await n._dispatch_stages(ev, now)
        sched.mark_missed.assert_called_once_with(5)
        assert "event_5_missed" in n._notified

    @pytest.mark.asyncio
    async def test_missed_skipped_if_already_completed(self):
        completed_ev = SimpleNamespace(
            id=6, title="X", category="fitness", status="completed",
            start_ts_utc=time.time() - 1800, end_ts_utc=time.time(),
            notes="", reminder_profile="fitness",
        )
        sched = MagicMock()
        sched.get_event_by_id = MagicMock(return_value=completed_ev)
        sched.mark_missed = MagicMock()
        n = _make_notifier(schedule_service=sched)
        now = time.time()
        ev = _ev(6, now - 30 * 60)
        await n._dispatch_stages(ev, now)
        sched.mark_missed.assert_not_called()
