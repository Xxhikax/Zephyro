"""Tests para extensiones de ScheduleService: reminder_profile, mark_missed, callbacks."""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def svc(tmp_path):
    from modules.schedule.service import ScheduleService
    db = tmp_path / f"sched_{uuid.uuid4().hex}.db"
    return ScheduleService(db_path=str(db))


class TestFitnessCategoryRegistered:
    def test_fitness_in_default_categories(self):
        from modules.schedule.service import DEFAULT_CATEGORIES
        assert "fitness" in DEFAULT_CATEGORIES


class TestReminderProfileColumn:
    def test_event_dataclass_has_reminder_profile(self):
        from modules.schedule.service import ScheduleEvent
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ScheduleEvent)}
        assert "reminder_profile" in fields

    def test_create_event_stores_reminder_profile(self, svc):
        now = time.time()
        ev = svc.create_event(
            user_id="admin", title="Entreno", category="fitness",
            start_ts_utc=now + 3600, end_ts_utc=now + 7200,
            reminder_profile="fitness",
        )
        assert ev.reminder_profile == "fitness"

        fresh = svc.get_event_by_id(ev.id)
        assert fresh is not None
        assert fresh.reminder_profile == "fitness"

    def test_create_event_default_profile_is_none(self, svc):
        now = time.time()
        ev = svc.create_event(
            user_id="admin", title="Algo", category="other",
            start_ts_utc=now + 3600, end_ts_utc=now + 7200,
        )
        assert ev.reminder_profile is None

    def test_create_recurring_series_propagates_reminder_profile(self, svc):
        now = time.time()
        events = svc.create_recurring_series(
            user_id="admin", title="Entreno semanal", category="fitness",
            start_ts_utc=now + 86400, end_ts_utc=now + 86400 + 3600,
            recurrence="weekly", weeks=3,
            reminder_profile="fitness",
        )
        assert len(events) == 3
        for ev in events:
            assert ev.reminder_profile == "fitness"


class TestMarkMissed:
    def test_mark_missed_changes_status(self, svc):
        now = time.time()
        ev = svc.create_event("admin", "X", "fitness", now + 100, now + 200)
        assert ev.status == "pending"
        ok = svc.mark_missed(ev.id)
        assert ok is True
        fresh = svc.get_event_by_id(ev.id)
        assert fresh.status == "missed"

    def test_mark_missed_no_op_on_completed(self, svc):
        now = time.time()
        ev = svc.create_event("admin", "X", "fitness", now + 100, now + 200)
        svc.mark_completed(ev.id)
        ok = svc.mark_missed(ev.id)
        assert ok is False  # solo cambia si status='pending'


class TestCallbackRegistry:
    def test_register_event_callback_exists(self):
        from modules.schedule.service import register_event_callback
        assert callable(register_event_callback)

    def test_register_invalid_type_raises(self):
        from modules.schedule.service import register_event_callback
        with pytest.raises(ValueError):
            register_event_callback("garbage", lambda x: None)

    def test_create_event_fires_created_callback(self, svc):
        from modules.schedule.service import register_event_callback
        seen = []

        def cb(ev):
            seen.append(ev.id)

        register_event_callback("created", cb)
        now = time.time()
        ev = svc.create_event("admin", "Y", "fitness", now + 100, now + 200)
        # callback corre en thread fire-and-forget — dar un margen
        time.sleep(0.1)
        assert ev.id in seen
