"""Telemetry WebSocket — real-time system metrics for the login HUD."""

import asyncio
import logging
import time

import psutil
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
log = logging.getLogger("zephyro.telemetry")

_boot_time = time.time()
_failed_attempts: int = 0
_last_events: list[str] = []


def record_failed_auth() -> None:
    global _failed_attempts
    _failed_attempts += 1
    _push_event("auth_failed")


def record_event(event: str) -> None:
    _push_event(event)


def _push_event(event: str) -> None:
    _last_events.insert(0, event)
    if len(_last_events) > 12:
        _last_events.pop()


def _fmt_uptime() -> str:
    s = int(time.time() - _boot_time)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _collect_snapshot(app) -> dict:
    cpu = psutil.cpu_percent(interval=0)
    mem = psutil.virtual_memory()
    mem_free_gb = round(mem.available / (1024 ** 3), 1)
    mem_total_gb = round(mem.total / (1024 ** 3), 1)

    # GPU temp + VRAM — best-effort via nvidia-smi
    gpu_temp = None
    vram_used_gb = None
    vram_total_gb = None
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            timeout=2, stderr=subprocess.DEVNULL,
        )
        parts = out.decode().strip().split("\n")[0].split(",")
        gpu_temp = int(parts[0].strip())
        vram_used_gb = round(int(parts[1].strip()) / 1024, 1)
        vram_total_gb = round(int(parts[2].strip()) / 1024, 1)
    except Exception:
        pass

    # Network throughput — best-effort
    net_up_kbps = None
    try:
        c1 = psutil.net_io_counters()
        # Store previous snapshot for delta calculation
        prev = getattr(_collect_snapshot, '_prev_net', None)
        prev_t = getattr(_collect_snapshot, '_prev_net_t', None)
        now_t = time.time()
        _collect_snapshot._prev_net = c1
        _collect_snapshot._prev_net_t = now_t
        if prev and prev_t:
            dt = now_t - prev_t
            if dt > 0:
                net_up_kbps = round((c1.bytes_sent - prev.bytes_sent) / dt / 1024, 1)
    except Exception:
        pass

    # Inference status
    inference = "OFFLINE"
    st = {}
    try:
        orch = getattr(app.state, "orchestrator", None)
        if orch:
            st = orch.llm.get_status()
            if st.get("local_available"):
                inference = "READY"
            elif st.get("cloud_available"):
                inference = "CLOUD_ONLY"
            else:
                inference = "DEGRADED"
    except Exception:
        pass

    # Vector count
    vectors = 0
    try:
        orch = getattr(app.state, "orchestrator", None)
        if orch and hasattr(orch, "context"):
            vs = getattr(orch.context, "vector_store", None)
            if vs and hasattr(vs, "count"):
                vectors = vs.count()
    except Exception:
        pass

    # Active modules
    modules = []
    try:
        orch = getattr(app.state, "orchestrator", None)
        if orch:
            if getattr(orch, "context", None):
                modules.append("memory.context")
            if st.get("local_available"):
                modules.append("ollama.local")
            if st.get("cloud_available"):
                modules.append(f"cloud.{st.get('cloud_provider', 'auto')}")
        living = getattr(app.state, "living_state", None)
        if living:
            modules.append("living_state")
        cm = getattr(app.state, "channel_manager", None)
        if cm:
            for name in cm.list_channels():
                ch = cm.get_channel(name)
                if ch and getattr(ch, "_running", False):
                    modules.append(f"{name}.bridge")
    except Exception:
        pass

    if not modules:
        modules = ["fastapi.gateway"]

    # Agent routing
    routing = [{"tier": 1, "name": "local.ollama"}]
    try:
        orch = getattr(app.state, "orchestrator", None)
        if orch:
            provider = orch.llm.cloud_provider or "auto"
            from core.model_catalog import ANTHROPIC_DEFAULT_MODEL
            model = getattr(orch.llm, "anthropic_model", ANTHROPIC_DEFAULT_MODEL)
            routing = [
                {"tier": 1, "name": "local.ollama"},
                {"tier": 2, "name": "haiku 4.5"},
                {"tier": 3, "name": model.replace("claude-", "")},
                {"tier": 4, "name": f"cloud.{provider}"},
            ]
    except Exception:
        pass

    return {
        "uptime": _fmt_uptime(),
        "telemetry": {
            "cpu_load": round(cpu / 100, 2),
            "mem_free_gb": mem_free_gb,
            "mem_total_gb": mem_total_gb,
            "gpu_temp": gpu_temp,
            "vram_used_gb": vram_used_gb,
            "vram_total_gb": vram_total_gb,
            "net_up_kbps": net_up_kbps,
            "net_latency": None,
            "inference": inference,
            "vectors": vectors,
        },
        "modules": modules,
        "events": _last_events[:6],
        "routing": routing,
        "security": {
            "failed_attempts": _failed_attempts,
            "lockout_in": max(0, 5 - _failed_attempts),
        },
    }


# Push event at startup
_push_event("core_boot")


@router.websocket("/system/telemetry")
async def telemetry_ws(ws: WebSocket):
    await ws.accept()
    app = ws.app
    try:
        while True:
            # `to_thread`: `_collect_snapshot` spawnea `nvidia-smi` (hasta 2 s
            # si el driver está ocupado) y lee psutil. Corría en el event loop
            # cada 3 s y POR CLIENTE conectado — con el panel abierto, el
            # backend pagaba un proceso nuevo cada 3 segundos sobre el loop.
            snapshot = await asyncio.to_thread(_collect_snapshot, app)
            await ws.send_json(snapshot)
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("Telemetry WS closed: %s", exc)
