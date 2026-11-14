#!/usr/bin/env python3
"""Zephyro Web App — FastAPI server (refactored)"""

import sys

# Fix Unicode emoji crash on Windows cp1252 consoles (Rich + emoji = crash)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio

# On Windows, uvicorn defaults to SelectorEventLoop which does not support
# asyncio.create_subprocess_exec (used by Playwright and several internal modules).
# ProactorEventLoop is required for subprocess support on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from core.logging_config import configure_logging
from core.session_store import load_sessions, save_sessions

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=True)

# Crear directorio de logs antes de configurar handlers
_LOGS_DIR = Path(__file__).parent.parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

configure_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    log_file=os.getenv(
        "ZEPHYRO_LOG_FILE",
        str(_LOGS_DIR / "backend.log"),
    ),
)
log = logging.getLogger("zephyro.web")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SESSION_TTL = timedelta(hours=float(os.getenv("SESSION_TTL_HOURS", "24")))

sessions: dict = {}


async def _session_cleanup_loop():
    """Elimina sesiones sin actividad más antigua que SESSION_TTL cada 5 minutos."""
    while True:
        try:
            await asyncio.sleep(300)
            now = datetime.now()
            expired = [
                t for t, s in list(sessions.items())
                if now - s.get("last_seen", s.get("created_at", now)) > SESSION_TTL
            ]
            for t in expired:
                sessions.pop(t, None)
            if expired:
                log.info(f"[session-gc] Eliminated {len(expired)} expired sessions")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning(f"[session-gc] Error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global sessions
    sessions.update(await load_sessions(SESSION_TTL))
    from core.orchestrator import Orchestrator
    app.state.orchestrator = Orchestrator(cloud_provider="auto")
    asyncio.create_task(_session_cleanup_loop())
    # Pre-warm STT in background so the first call-mode turn doesn't pay the Whisper
    # cold-load cost — but ONLY when the active STT engine is local. With a cloud STT
    # (GROQ) loading whisper-medium just wastes ~2.6GB VRAM; _get_stt() is lazy and will
    # load on demand if the user switches to local STT at runtime.
    from routers.voice import get_stt_engine
    if get_stt_engine() == "local":
        asyncio.create_task(app.state.orchestrator.warm_stt())

    # LivingState — estado interno reactivo
    from core.event_bus import get_bus
    from core.living_state import LivingState

    _living = LivingState(
        db_path=str(Path(__file__).parent / "storage" / "living_state.db"),
        code_root=str(Path(__file__).parent.parent),
        log_file=os.getenv("ZEPHYRO_LOG_FILE", str(_LOGS_DIR / "backend.log")),
    )
    app.state.living_state = _living
    await _living.start()

    # Auto-diagnose subscriber: LOG_ERROR → DiagnosticAgent automático
    # Debounce: máx 1 diagnóstico por módulo cada 5 minutos para evitar spam.
    _diag_cooldown: dict[str, float] = {}
    _DIAG_COOLDOWN_SECS = 300

    async def _on_auto_diagnose(payload: dict) -> None:
        import time as _time
        # Solo errores — ignorar warnings para evitar spam
        if payload.get("level", "").upper() != "ERROR":
            return
        module = payload.get("module") or payload.get("name") or "unknown"
        now = _time.time()
        if now - _diag_cooldown.get(module, 0.0) < _DIAG_COOLDOWN_SECS:
            return
        _diag_cooldown[module] = now

        gate = getattr(app.state, "diagnostic_gate", None)
        if gate is None:
            return
        try:
            from subagents.diagnostic_agent import DiagnosticAgent
            agent = DiagnosticAgent(
                llm_manager=app.state.orchestrator.llm,
                gate=gate,
                log_file=os.getenv("ZEPHYRO_LOG_FILE", str(_LOGS_DIR / "backend.log")),
                code_root=str(Path(__file__).parent),
            )
            # Obtener un user_id válido para el gate
            cm = getattr(app.state, "channel_manager", None)
            tg = cm.get_channel("telegram") if cm else None
            chat_id = next(iter(getattr(tg, "_authorized_ids", [])), 0)
            await agent.run(chat_id=chat_id, n_lines=100)
        except Exception as exc:
            log.warning("[auto-diagnose] Error: %s", exc)

    get_bus().subscribe("auto_diagnose", _on_auto_diagnose)

    # Channel Manager — Telegram, WhatsApp, etc.
    from channels.manager import ChannelManager
    app.state.channel_manager = ChannelManager(app.state.orchestrator)
    await app.state.channel_manager.auto_register()
    await app.state.channel_manager.start_all()

    # DiagnosticAgent bridge (Tauri): store + WS gate
    from core.connection_registry import registry as _conn_registry
    from subagents.diagnostic_store import DiagnosticStore
    from subagents.ws_approval_gate import WebSocketApprovalGate
    _diag_store = DiagnosticStore()
    _diag_store.cleanup_expired(max_age_hours=1.0)
    app.state.diagnostic_store = _diag_store
    app.state.diagnostic_gate = WebSocketApprovalGate(_conn_registry, _diag_store)
    # Make gate + store + log path reachable from the orchestrator so the
    # "diagnostic" intent handler can spawn DiagnosticAgent in background.
    app.state.orchestrator.diagnostic_gate = app.state.diagnostic_gate
    app.state.orchestrator.diagnostic_store = app.state.diagnostic_store
    app.state.orchestrator.diagnostic_log_file = os.getenv(
        "ZEPHYRO_LOG_FILE", str(_LOGS_DIR / "backend.log")
    )
    # WS registry for orchestrator broadcasts (diagnostic_running indicator, etc.)
    app.state.orchestrator._ws_registry = _conn_registry

    # VerificationAgent — suscriptor de SKILL_EXECUTED, fire-and-forget
    from agents.verification_agent import VerificationAgent
    from core.event_bus import SKILL_EXECUTED, AUTOMATION_EXECUTED
    _verification_agent = VerificationAgent(
        conn_registry=_conn_registry,
        code_root=str(Path(__file__).parent.parent),
        audit_path=str(_LOGS_DIR / "verification_audit.jsonl"),
    )
    get_bus().subscribe(SKILL_EXECUTED, _verification_agent.on_skill_executed)
    get_bus().subscribe(AUTOMATION_EXECUTED, _verification_agent.on_automation_executed)
    app.state.verification_agent = _verification_agent

    # OrchestratorAgent — wrapper sobre orchestrator existente (no lo reemplaza)
    from agents.orchestrator_agent import OrchestratorAgent
    app.state.orchestrator_agent = OrchestratorAgent(app.state.orchestrator)

    # TerminalController — singleton para skills que ejecutan PowerShell.
    # Usa un WebSocketApprovalGate dedicado con prefix "terminal" para
    # distinguir su UI del DiagnosticAgent (que usa prefix "diag").
    from modules.automation.terminal_controller import (
        TerminalController, set_terminal_controller,
    )
    _terminal_gate = WebSocketApprovalGate(
        _conn_registry, _diag_store, event_prefix="terminal",
    )
    app.state.terminal_gate = _terminal_gate
    _terminal = TerminalController(
        approval_gate=_terminal_gate,
        connection_registry=_conn_registry,
    )
    set_terminal_controller(_terminal)
    app.state.terminal_controller = _terminal

    # Crear workspace de repos en D:\ (con fallback a ~/zephyro_workspace)
    _repos_root = Path(os.getenv("ZEPHYRO_REPOS_ROOT", "D:/zephyro_repos"))
    try:
        _repos_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        _repos_root = Path.home() / "zephyro_workspace" / "repos"
        _repos_root.mkdir(parents=True, exist_ok=True)
        log.warning("D:\\ no disponible. Repos workspace en %s", _repos_root)
    app.state.repos_root = _repos_root

    # Scheduler — Parte B (Hermes-style learning) + tareas periódicas migradas.
    from core.scheduler import get_scheduler
    from core.task_envelope import get_envelope_store
    _scheduler = get_scheduler()
    _envelope_store = get_envelope_store()
    app.state.scheduler = _scheduler
    app.state.envelope_store = _envelope_store

    # Cleanup de envelopes viejos diario a las 4am
    _scheduler.add_cron(
        "envelopes_cleanup",
        lambda: _envelope_store.cleanup_old(max_age_days=30.0),
        hour=4, minute=0,
    )

    # SkillProposer — destila TaskEnvelopes en drafts cada 30min
    from modules.learning.skill_proposer import get_skill_proposer
    _proposer = get_skill_proposer()
    app.state.skill_proposer = _proposer
    # llm_caller se inyecta cuando esté disponible (deferred)
    if hasattr(app.state.orchestrator, "llm"):
        async def _proposer_llm(prompt: str) -> str:
            try:
                # Reusa el LLMManager del orchestrator con tier alto (cloud)
                resp = await app.state.orchestrator.llm.invoke(
                    prompt, force_cloud=True,
                )
                return str(resp) if resp else ""
            except Exception as exc:
                log.warning("[skill_proposer] llm_caller falló: %s", exc)
                return ""
        _proposer._llm_caller = _proposer_llm
    _scheduler.add_interval("skill_proposer_nudge", _proposer.nudge, minutes=30)

    # MemoryArchival — diaria a las 3am
    from modules.memory_extended.memory_archival import get_memory_archival
    _archival = get_memory_archival()
    app.state.memory_archival = _archival
    _scheduler.add_cron("memory_archival", _archival.move_cold, hour=3, minute=0)

    # Reflection engine — force_reflect every 6h via scheduler
    async def _scheduled_reflect():
        _orch = app.state.orchestrator
        _re = getattr(_orch, "reflection_engine", None)
        if _re is None:
            return
        try:
            _recent = _orch.context.get_recent_conversations(n=20) if _orch.context else []
            _profile = ""
            if getattr(_orch, "user_profile", None):
                try:
                    _profile = _orch.user_profile.get_summary()
                except Exception:
                    pass
            _forced_count = _re._last_reflected_at + _re.reflect_every + 1
            _llm_fn = lambda p: _orch.llm.generate(p, force_cloud=True)
            await asyncio.get_event_loop().run_in_executor(
                None, _re.maybe_reflect,
                _forced_count, _recent, _profile, _llm_fn,
            )
        except Exception as _exc:
            log.warning("[scheduler] reflection failed: %s", _exc)

    _scheduler.add_interval("reflection_engine", _scheduled_reflect, hours=6)
    _scheduler.start()

    # ── Nutrition cron jobs ─────────────────────────────────────────────────
    try:
        from modules.nutrition.reminders import (
            remind_breakfast, remind_lunch_if_missing, remind_water_if_behind,
            daily_nutrition_summary, end_of_day_protein_check, remind_steps_if_unlogged,
        )
        _NUTR_USER = "admin"
        _scheduler.register_named_job(
            "nutrition_breakfast",
            lambda: remind_breakfast(_NUTR_USER),
            trigger="cron", hour=7, minute=30,
        )
        _scheduler.register_named_job(
            "nutrition_lunch",
            lambda: remind_lunch_if_missing(_NUTR_USER),
            trigger="cron", hour=13, minute=0,
        )
        _scheduler.register_named_job(
            "nutrition_water_pace",
            lambda: remind_water_if_behind(_NUTR_USER),
            trigger="cron", hour="8-20/2", minute=0,
        )
        _scheduler.register_named_job(
            "nutrition_summary",
            lambda: daily_nutrition_summary(_NUTR_USER),
            trigger="cron", hour=20, minute=0,
        )
        _scheduler.register_named_job(
            "nutrition_eod",
            lambda: end_of_day_protein_check(_NUTR_USER),
            trigger="cron", hour=23, minute=0,
        )
        _scheduler.register_named_job(
            "steps_unlogged",
            lambda: remind_steps_if_unlogged(_NUTR_USER),
            trigger="cron", hour=19, minute=0,
        )
        log.info("[startup] nutrition cron jobs registrados (6 jobs)")
    except Exception as exc:
        log.warning("[startup] nutrition reminders no registrados: %s", exc)

    # ── Fitness service wiring (vision + channel manager + llm) ─────────────
    try:
        from modules.fitness.service import get_fitness_service
        _fit = get_fitness_service()
        _fit.set_llm_manager(app.state.orchestrator.llm)
        _vision = getattr(app.state.orchestrator, "vision_analyze", None)
        if _vision is not None:
            _fit.set_image_analyzer(_vision)
        _fit.set_channel_manager(app.state.channel_manager)
        log.info("[startup] fitness service wired (llm + vision + channels)")
    except Exception as exc:
        log.warning("[startup] fitness wiring falló: %s", exc)

    # ── Nutrition service wiring (vision + channel manager + llm) ───────────
    try:
        from modules.nutrition.service import get_nutrition_service
        _nut = get_nutrition_service()
        if hasattr(_nut, "set_llm_manager"):
            _nut.set_llm_manager(app.state.orchestrator.llm)
        _vision = getattr(app.state.orchestrator, "vision_analyze", None)
        if _vision is not None and hasattr(_nut, "set_image_analyzer"):
            _nut.set_image_analyzer(_vision)
        if hasattr(_nut, "set_channel_manager"):
            _nut.set_channel_manager(app.state.channel_manager)
        log.info("[startup] nutrition service wired (llm + vision + channels)")
    except Exception as exc:
        log.warning("[startup] nutrition wiring falló: %s", exc)

    # El TaskEnvelope (loop Hermes) ahora lo graba el orchestrator dentro de
    # handle_input vía _finalize_envelope — con intent real, steps reales y
    # success real. Acá solo inyectamos el store; el wrapper queda únicamente
    # para el study-session tracker.
    app.state.orchestrator.set_envelope_store(_envelope_store)
    _orig_handle = app.state.orchestrator.handle_input

    async def _handle_with_envelope(user_input, ctx=None, **kwargs):
        _response = await _orig_handle(user_input, ctx=ctx, **kwargs)
        # Study session break reminder tracking
        try:
            from core.orchestrator import _STUDY_INTENT_RE
            _orch = app.state.orchestrator
            if _STUDY_INTENT_RE.search(user_input):
                import time as _t_mod
                if _orch._study_start is None:
                    _orch._study_start = _t_mod.time()
                    _orch._study_notified = False
                asyncio.ensure_future(_orch._push_break_if_due())
            else:
                _orch._study_start = None
                _orch._study_notified = False
        except Exception as _exc:
            log.debug("[study-tracker] error: %s", _exc)
        return _response

    app.state.orchestrator.handle_input = _handle_with_envelope

    # Phase 3: Inicializar Consciousness con getter lazy de LivingState
    from core.consciousness import get_consciousness
    _consciousness = get_consciousness()
    _consciousness.initialize(lambda: getattr(app.state, "living_state", None))

    # Inyectar canal Telegram en LivingState para notificaciones
    tg_channel = app.state.channel_manager.get_channel("telegram")
    if tg_channel is not None:
        _living.set_telegram_channel(tg_channel)
        tg_channel.set_living_state(_living)
        tg_channel.set_consciousness(_consciousness)

    # ProactiveNotifier — push de recordatorios y eventos próximos
    from core.proactive_notifier import ProactiveNotifier
    from core.connection_registry import registry as ws_registry

    _reminder_system = None
    _reminders_skill = app.state.orchestrator.skill_registry.get_skill("reminders")
    if _reminders_skill and hasattr(_reminders_skill, "system"):
        _reminder_system = _reminders_skill.system

    _notifier = ProactiveNotifier(
        schedule_service=app.state.orchestrator.schedule_service,
        reminder_system=_reminder_system,
        channel_manager=app.state.channel_manager,
        ws_registry=ws_registry,
        user_id="admin",
    )
    _notifier_task = asyncio.create_task(_notifier.run_loop())
    app.state.notifier = _notifier

    # Inyectar CanvasClient para notificaciones de tareas
    try:
        from modules.canvas.client import CanvasClient
        _notifier.canvas_client = CanvasClient()
        log.info("[notifier] CanvasClient inyectado en ProactiveNotifier")
    except Exception as exc:
        log.info(f"[notifier] CanvasClient no disponible para notificaciones: {exc}")

    _briefing_skill = app.state.orchestrator.skill_registry.get_skill("daily-briefing")
    if _briefing_skill and hasattr(_briefing_skill, "set_notifier"):
        _briefing_skill.set_notifier(_notifier)
        _notifier.briefing_skill = _briefing_skill
        log.info("[notifier] DailyBriefingSkill inyectada en ProactiveNotifier")

    # Inyectar WS registry + ContextDetector + AppController en FocusMonitor
    try:
        from modules.schedule.monitor import get_focus_monitor
        from core.context_detector import detector as _ctx_det
        _fm = get_focus_monitor()
        _fm.set_ws_registry(ws_registry)
        # Limpiar cualquier sesión Pomodoro huérfana de un proceso anterior
        # (un asyncio task no sobrevive a un reinicio). El frontend además se
        # auto-sana con GET /pomodoro/status al cargar/reconectar.
        try:
            _orphan = await _fm.recover_orphan_session()
            if _orphan:
                log.info(f"[focus] sesión Pomodoro huérfana limpiada: {_orphan.get('task','')!r}")
        except Exception as _exc:
            log.info(f"[focus] recover_orphan_session falló: {_exc}")
        _fm.set_context_detector(_ctx_det)
        _orch = getattr(app.state, "orchestrator", None)
        if _orch:
            _fm.set_app_controller(getattr(_orch, "automation", None))
            _vc = getattr(_orch, "vision_capture", None)
            _va = getattr(_orch, "vision_analyze", None)
            if _vc and _va:
                _fm.set_vision_tools(_vc, _va)
        log.info("[focus] WS registry + ContextDetector + vision inyectados en FocusMonitor")
    except Exception as exc:
        log.warning(f"[focus] No se pudo inyectar dependencias en FocusMonitor: {exc}")

    from core.browser_log_watcher import watcher as _browser_log_watcher
    await _browser_log_watcher.start()
    app.state.browser_log_watcher = _browser_log_watcher

    # ── ContextDetector — shell contextual ─────────────────────────────────
    from core.context_detector import detector as _context_detector
    from core.connection_registry import registry as _conn_registry

    async def _get_browser_url() -> str | None:
        """Best-effort: lee la URL actual del BrowserController si ya está vivo."""
        try:
            orch = getattr(app.state, "orchestrator", None)
            if not orch:
                return None
            automation = getattr(orch, "automation", None)
            if not automation:
                return None
            # No forzar lazy-init: revisar _browser interno
            browser = getattr(automation, "_browser", None)
            if browser is None:
                return None
            page = getattr(browser, "page", None)
            if page is None or page.is_closed():
                return None
            return page.url
        except Exception:
            return None

    _ws_store = getattr(app.state.orchestrator, "workspace", None)
    _context_detector.configure(
        registry=_conn_registry,
        get_browser_url_fn=_get_browser_url,
        workspace_store=_ws_store,
    )
    await _context_detector.start()
    app.state.context_detector = _context_detector

    log.info("Zephyro API lista.")
    yield
    try:
        await _context_detector.stop()
    except Exception:
        pass
    try:
        await _browser_log_watcher.stop()
    except Exception:
        pass
    _notifier_task.cancel()
    try:
        await _notifier_task
    except asyncio.CancelledError:
        pass
    await app.state.channel_manager.stop_all()
    await _living.stop()
    try:
        app.state.scheduler.shutdown()
    except Exception:
        pass
    try:
        await app.state.terminal_controller.shutdown()
    except Exception:
        pass
    await save_sessions(sessions)
    log.info("Sessions saved. Shutting down.")


app = FastAPI(title="Zephyro Web App", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


class NoCacheMiddleware:
    """Pure ASGI middleware — passes WebSocket connections through untouched."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and scope["path"].startswith("/static/"):
                headers = dict(message.get("headers", []))
                extra = [
                    (b"cache-control", b"no-cache, no-store, must-revalidate"),
                    (b"pragma", b"no-cache"),
                    (b"expires", b"0"),
                ]
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(NoCacheMiddleware)

# Mount static files
_static = Path("web/static")
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

# Include routers
from routers import auth, chat, voice, vision, code, search, memory, workspace, files, self_state, system, channels, skills, telemetry, diagnostic, terminal, providers, schedule, canvas as canvas_router, agents as agents_router, browser as browser_router, context as context_router, automation, fitness as fitness_router, nutrition as nutrition_router

app.include_router(auth.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(voice.router, prefix="/api")
app.include_router(vision.router, prefix="/api")
app.include_router(code.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(workspace.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(self_state.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
app.include_router(skills.router, prefix="/api")
app.include_router(telemetry.router, prefix="/api")
app.include_router(diagnostic.router, prefix="/api")
app.include_router(terminal.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(schedule.router, prefix="/api")
app.include_router(canvas_router.router, prefix="/api")
app.include_router(agents_router.router, prefix="/api")
app.include_router(browser_router.router, prefix="/api")
app.include_router(context_router.router, prefix="/api")
app.include_router(automation.router, prefix="/api")
app.include_router(fitness_router.router, prefix="/api")
app.include_router(nutrition_router.router, prefix="/api")


@app.get("/")
async def read_root():
    """Servir página principal"""
    html_path = Path("web/index.html")
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(), status_code=200)
    return HTMLResponse(content="<h1>Zephyro Web App</h1><p>Archivo index.html no encontrado</p>", status_code=404)


# ============ INICIAR SERVIDOR ============

if __name__ == "__main__":
    import sys
    import uvicorn

    # --service: modo daemon (Task Scheduler / NSSM). Escribe PID a archivo para scripts externos.
    if "--service" in sys.argv:
        Path(__file__).parent.parent.joinpath("backend.pid").write_text(str(os.getpid()))
        log.info("Zephyro arrancando en modo servicio (PID %d)", os.getpid())

    use_https = "--https" in sys.argv or os.getenv("ZEPHYRO_USE_HTTPS", "").lower() == "true"

    if use_https:
        ssl_cert_file = os.getenv("SSL_CERT_FILE", "./ssl_certs/cert.pem")
        ssl_key_file = os.getenv("SSL_KEY_FILE", "./ssl_certs/key.pem")
        port = int(os.getenv("HTTPS_PORT", "8443"))

        if not os.path.exists(ssl_cert_file) or not os.path.exists(ssl_key_file):
            log.error("SSL certificates not found. Run: ./generate_ssl_cert.sh")
            sys.exit(1)

        log.info(f"Starting Zephyro Web App with HTTPS on port {port}...")
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            ssl_certfile=ssl_cert_file,
            ssl_keyfile=ssl_key_file
        )
    else:
        port = int(os.getenv("HTTP_PORT", "8000"))
        log.info(f"Starting Zephyro Web App (HTTP) on port {port}...")
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
