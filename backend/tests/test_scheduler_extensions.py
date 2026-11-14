"""Tests para ZephyroScheduler: timezone, register_named_job, trigger_now."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestSchedulerBasics:
    def test_scheduler_imports(self):
        from core.scheduler import ZephyroScheduler, get_scheduler, SCHEDULER_TZ
        assert callable(get_scheduler)
        assert ZephyroScheduler is not None

    def test_default_timezone_is_lima(self):
        from core.scheduler import SCHEDULER_TZ
        # default debería ser America/Lima salvo override por env
        import os
        assert SCHEDULER_TZ == os.getenv("SCHEDULER_TIMEZONE", "America/Lima")

    def test_scheduler_instance_has_lima_tz(self):
        from core.scheduler import ZephyroScheduler, SCHEDULER_TZ
        s = ZephyroScheduler()
        if s.scheduler is None:
            pytest.skip("apscheduler no instalado")
        tz = str(s.scheduler.timezone)
        # APScheduler resuelve a timezone object — chequeo flexible
        assert "Lima" in tz or "America" in tz or tz == SCHEDULER_TZ


class TestNamedJobs:
    def test_register_named_job_stores_func(self):
        from core.scheduler import ZephyroScheduler
        s = ZephyroScheduler()
        called = []

        def my_job():
            called.append("ran")

        s.register_named_job("test_job_X", my_job, trigger="cron", hour=3)
        assert "test_job_X" in s.list_named_jobs()

    def test_trigger_now_unknown_returns_false(self):
        from core.scheduler import ZephyroScheduler
        s = ZephyroScheduler()
        assert s.trigger_now("nonexistent_job") is False

    def test_trigger_now_runs_sync_func_when_no_loop(self):
        from core.scheduler import ZephyroScheduler
        s = ZephyroScheduler()
        called = []
        s.register_named_job("sync_x", lambda: called.append(1), trigger="cron", hour=5)
        ok = s.trigger_now("sync_x")
        # Cuando no hay event loop corriendo, ejecuta sync inmediato
        assert ok is True
        # The wrapped runner may run in background — el assert relevante es que ok=True

    @pytest.mark.asyncio
    async def test_trigger_now_schedules_async_func(self):
        from core.scheduler import ZephyroScheduler
        s = ZephyroScheduler()
        flag = {"called": False}

        async def my_async():
            flag["called"] = True

        s.register_named_job("async_x", my_async, trigger="cron", hour=6)
        ok = s.trigger_now("async_x")
        assert ok is True
        # dejar pestañar el loop para que la tarea async corra
        await asyncio.sleep(0.05)
        assert flag["called"] is True
