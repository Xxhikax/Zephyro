"""Automation router — disparar jobs cron registrados on-demand.

Pensado para integración futura con n8n u otros orchestrators externos:
un POST a /api/automation/trigger/{job_name} ejecuta el job inmediatamente
sin tener que esperar a su próximo cron tick.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from core.auth_middleware import require_auth
from core.scheduler import get_scheduler

router = APIRouter()


@router.get("/automation/jobs")
def list_jobs(request: Request, session: dict = Depends(require_auth)):
    """Lista los named jobs registrados (útil para debugging)."""
    sched = get_scheduler()
    return {
        "named_jobs": sched.list_named_jobs(),
        "all_jobs": sched.list_jobs(),
    }


@router.post("/automation/trigger/{job_name}")
def trigger_job(job_name: str, request: Request, session: dict = Depends(require_auth)):
    """Dispara un job nombrado por su nombre. Idempotente desde el lado del caller."""
    sched = get_scheduler()
    ok = sched.trigger_now(job_name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"job '{job_name}' no registrado")
    return {"ran": True, "job": job_name}
