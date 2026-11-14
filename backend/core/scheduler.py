"""
ZephyroScheduler — wrapper async sobre APScheduler para tareas periódicas:
    - reflection_engine.force_reflect()  cada 6h
    - self_state.dump_capabilities()      cada 24h
    - skill_proposer.nudge()              cada 30min
    - memory_archival.move_cold()         cada noche a las 3am
    - reminders                           cada 60s (migrado de threading.Thread)
    - task_envelope_store.cleanup_old()   diario a las 4am

Cada job protegido con try/except + log. Inicializado en lifespan.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any, Callable, Optional

log = logging.getLogger("zephyro.scheduler")

SCHEDULER_TZ = os.getenv("SCHEDULER_TIMEZONE", "America/Lima")


class ZephyroScheduler:

    def __init__(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError:
            log.warning("[scheduler] apscheduler no instalado — scheduler deshabilitado")
            self.scheduler = None
            self._jobs: list[str] = []
            self._named_funcs: dict[str, Callable] = {}
            return
        try:
            self.scheduler = AsyncIOScheduler(timezone=SCHEDULER_TZ)
        except Exception as exc:
            # Si pytz/zoneinfo no resuelve el TZ, caer a default sin romper
            log.warning("[scheduler] timezone %s no disponible (%s) — usando default", SCHEDULER_TZ, exc)
            self.scheduler = AsyncIOScheduler()
        self._jobs: list[str] = []
        self._named_funcs: dict[str, Callable] = {}

    def add_interval(
        self,
        job_id: str,
        func: Callable[..., Any],
        *,
        hours: float = 0,
        minutes: float = 0,
        seconds: float = 0,
    ) -> None:
        if self.scheduler is None:
            return
        wrapped = self._wrap(func, job_id)
        self.scheduler.add_job(
            wrapped, "interval",
            id=job_id,
            hours=hours, minutes=minutes, seconds=seconds,
            replace_existing=True, max_instances=1, coalesce=True,
        )
        self._jobs.append(job_id)
        log.info("[scheduler] job añadido: %s (every %sh %sm %ss)",
                 job_id, hours, minutes, seconds)

    def add_cron(
        self,
        job_id: str,
        func: Callable[..., Any],
        *,
        hour=0, minute=0,
    ) -> None:
        if self.scheduler is None:
            return
        wrapped = self._wrap(func, job_id)
        self.scheduler.add_job(
            wrapped, "cron",
            id=job_id, hour=hour, minute=minute,
            replace_existing=True, max_instances=1, coalesce=True,
        )
        self._jobs.append(job_id)
        log.info("[scheduler] cron añadido: %s (hour=%s minute=%s tz=%s)",
                 job_id, hour, minute, SCHEDULER_TZ)

    def register_named_job(
        self,
        name: str,
        func: Callable[..., Any],
        *,
        trigger: str = "cron",
        **trigger_kwargs,
    ) -> None:
        """Registra un job nombrado e invocable on-demand via trigger_now.

        trigger ∈ {'cron','interval'}. trigger_kwargs pasa a APScheduler tal cual
        (hour, minute, hours, minutes, seconds, etc.).
        """
        if self.scheduler is None:
            self._named_funcs[name] = func
            return
        self._named_funcs[name] = func
        wrapped = self._wrap(func, name)
        try:
            self.scheduler.add_job(
                wrapped, trigger, id=name,
                replace_existing=True, max_instances=1, coalesce=True,
                **trigger_kwargs,
            )
            self._jobs.append(name)
            log.info("[scheduler] named job: %s (%s %s)", name, trigger, trigger_kwargs)
        except Exception as exc:
            log.warning("[scheduler] register_named_job %s falló: %s", name, exc)

    def trigger_now(self, name: str) -> bool:
        """Ejecuta on-demand un named job. Devuelve True si pudo dispararlo.

        Robusto a:
        - Loop corriendo (FastAPI): schedule coro como task.
        - Sin loop en este thread (worker pool, sync handler): si la func es
          async, corre con asyncio.run; si es sync, la ejecuta inline.
        """
        func = self._named_funcs.get(name)
        if func is None:
            log.warning("[scheduler] trigger_now: job '%s' no registrado", name)
            return False
        try:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            if running is not None:
                async def _runner():
                    try:
                        if inspect.iscoroutinefunction(func):
                            await func()
                        else:
                            res = func()
                            if inspect.iscoroutine(res):
                                await res
                    except Exception as exc:
                        log.exception("[scheduler] trigger_now %s falló: %s", name, exc)
                running.create_task(_runner())
            else:
                if inspect.iscoroutinefunction(func):
                    asyncio.run(func())
                else:
                    res = func()
                    if inspect.iscoroutine(res):
                        asyncio.run(res)
            return True
        except Exception as exc:
            log.exception("[scheduler] trigger_now %s falló: %s", name, exc)
            return False

    def list_named_jobs(self) -> list[str]:
        return list(self._named_funcs.keys())

    def remove(self, job_id: str) -> None:
        if self.scheduler is None:
            return
        try:
            self.scheduler.remove_job(job_id)
            self._jobs = [j for j in self._jobs if j != job_id]
        except Exception as exc:
            log.debug("[scheduler] remove %s: %s", job_id, exc)

    def start(self) -> None:
        if self.scheduler is None:
            return
        self.scheduler.start()
        log.info("[scheduler] iniciado con %d jobs", len(self._jobs))

    def shutdown(self) -> None:
        if self.scheduler is None or not self.scheduler.running:
            return
        try:
            self.scheduler.shutdown(wait=False)
            log.info("[scheduler] detenido")
        except Exception as exc:
            log.warning("[scheduler] shutdown error: %s", exc)

    def list_jobs(self) -> list[dict[str, Any]]:
        if self.scheduler is None:
            return []
        return [
            {
                "id": j.id,
                "next_run": str(j.next_run_time) if j.next_run_time else None,
                "trigger": str(j.trigger),
            }
            for j in self.scheduler.get_jobs()
        ]

    @staticmethod
    def _wrap(func: Callable[..., Any], job_id: str) -> Callable[[], Any]:
        import asyncio
        import inspect

        async def runner() -> None:
            try:
                if inspect.iscoroutinefunction(func):
                    await func()
                else:
                    result = func()
                    if inspect.iscoroutine(result):
                        await result
            except Exception as exc:
                log.exception("[scheduler] job %s falló: %s", job_id, exc)

        return runner


# ── Singleton ──────────────────────────────────────────────────────────

_scheduler: Optional[ZephyroScheduler] = None


def get_scheduler() -> ZephyroScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = ZephyroScheduler()
    return _scheduler


def reset_scheduler() -> None:
    """Solo para tests."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown()
    _scheduler = None
