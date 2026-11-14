"""System router — status, health, settings endpoints."""
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.auth_middleware import require_auth, require_admin

router = APIRouter()

# ── File-change watcher ───────────────────────────────────────────────────────
# Computes MD5 hashes of all .py files in backend dirs at startup.
# Every 5 s it re-hashes and sets _needs_restart when any file changes.
# This drives the frontend "⟳ RESTART" button visibility.

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_WATCH_SUBDIRS = ["core", "routers", "modules", "skills", "channels"]

_startup_hashes: dict = {}
_needs_restart: bool = False
_changed_files: list = []


def _hash_py_files() -> dict:
    result: dict = {}
    for sub in _WATCH_SUBDIRS:
        d = _BACKEND_DIR / sub
        if d.exists():
            for f in d.rglob("*.py"):
                try:
                    result[str(f)] = hashlib.md5(f.read_bytes()).hexdigest()
                except Exception:
                    pass
    return result


def _watcher_loop() -> None:
    global _needs_restart, _changed_files
    while True:
        time.sleep(5)
        current = _hash_py_files()
        changed = [f for f, h in current.items() if _startup_hashes.get(f) != h]
        if changed:
            _needs_restart = True
            _changed_files = changed


_startup_hashes = _hash_py_files()
threading.Thread(target=_watcher_loop, daemon=True, name="backend-watcher").start()
# ─────────────────────────────────────────────────────────────────────────────


def get_orchestrator(request: Request):
    return request.app.state.orchestrator


class SettingsUpdate(BaseModel):
    key: str
    value: str


@router.get("/status")
async def get_status(request: Request, session: dict = Depends(require_auth)):
    """Obtener estado del sistema"""
    orchestrator = get_orchestrator(request)
    llm_status = orchestrator.llm.get_status()

    from routers.voice import get_stt_engine, get_tts_engine

    return {
        "user": {
            "role": session["role"],
            "name": session.get("name"),
            "authenticated": session.get("authenticated", False)
        },
        "models": {
            "local_available": llm_status["local_available"],
            "cloud_available": llm_status["cloud_available"] if session["role"] == "admin" else False,
            "cloud_provider": llm_status.get("cloud_provider") if session["role"] == "admin" else None,
            "anthropic_model": orchestrator.llm.anthropic_model if session["role"] == "admin" else None,
            "deepseek_model": getattr(orchestrator.llm, "deepseek_model", None) if session["role"] == "admin" else None,
            "is_online": llm_status["is_online"]
        },
        "stt_engine": get_stt_engine() if session["role"] == "admin" else "local",
        "tts_engine": get_tts_engine() if session["role"] == "admin" else "local"
    }


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


_STOP_PHRASES_CACHE: Optional[dict] = None
_EXIT_PHRASES_CACHE: Optional[dict] = None


@router.get("/config/stop_phrases")
async def get_stop_phrases():
    """Frases de cancelación para barge-in en modo call. Cacheado en memoria."""
    global _STOP_PHRASES_CACHE
    if _STOP_PHRASES_CACHE is None:
        import json
        from pathlib import Path
        cfg = Path(__file__).resolve().parent.parent / "config" / "stop_phrases.json"
        try:
            _STOP_PHRASES_CACHE = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            _STOP_PHRASES_CACHE = {"es": [], "en": []}
    return _STOP_PHRASES_CACHE


@router.get("/config/exit_phrases")
async def get_exit_phrases():
    """Frases para cerrar el modo call entero. Cacheado en memoria."""
    global _EXIT_PHRASES_CACHE
    if _EXIT_PHRASES_CACHE is None:
        import json
        from pathlib import Path
        cfg = Path(__file__).resolve().parent.parent / "config" / "exit_phrases.json"
        try:
            _EXIT_PHRASES_CACHE = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            _EXIT_PHRASES_CACHE = {"es": [], "en": []}
    return _EXIT_PHRASES_CACHE


@router.get("/system/error-log")
async def get_error_log(request: Request, limit: int = 50, session: dict = Depends(require_admin)):
    """Retorna los errores recientes del sistema para el panel Tauri."""
    living_state = getattr(request.app.state, "living_state", None)
    if living_state is None:
        return {"errors": [], "telegram_enabled": False}

    errors = living_state.get_error_log(limit=limit)
    return {
        "errors": errors,
        "telegram_enabled": living_state._telegram_enabled(),
    }


@router.post("/settings/provider")
async def change_provider(request: Request, provider: str, session: dict = Depends(require_admin)):
    """Cambiar proveedor cloud"""
    if provider not in ["gemini", "openai", "anthropic", "deepseek", "auto"]:
        raise HTTPException(status_code=400, detail="Proveedor no válido")

    orchestrator = get_orchestrator(request)
    status = orchestrator.llm.set_cloud_provider(provider)

    return {
        "success": status["cloud_available"],
        "provider": status.get("cloud_provider"),
        "message": f"Proveedor cambiado a {provider}"
    }


@router.post("/settings/stt-engine")
async def change_stt_engine(request: Request, engine: str, session: dict = Depends(require_admin)):
    """Cambiar el motor de STT (local o groq)"""
    valid_engines = ["local", "groq"]
    if engine not in valid_engines:
        raise HTTPException(status_code=400, detail=f"Engine no válido. Opciones: {valid_engines}")

    from routers.voice import set_stt_engine
    set_stt_engine(engine)

    return {
        "success": True,
        "engine": engine,
        "message": f"STT engine cambiado a {engine}"
    }


@router.post("/settings/tts-engine")
async def change_tts_engine(request: Request, engine: str, session: dict = Depends(require_admin)):
    """Cambiar el motor de TTS (local o fishaudio)"""
    valid_engines = ["local", "fishaudio"]
    if engine not in valid_engines:
        raise HTTPException(status_code=400, detail=f"Engine no válido. Opciones: {valid_engines}")

    from routers.voice import set_tts_engine
    set_tts_engine(engine)

    return {
        "success": True,
        "engine": engine,
        "message": f"TTS engine cambiado a {engine}"
    }


@router.post("/settings/anthropic-model")
async def change_anthropic_model(request: Request, model: str, session: dict = Depends(require_admin)):
    """Cambiar el modelo de Anthropic (IDs legacy se remapean al tier vigente)"""
    from core.model_catalog import ANTHROPIC_VALID_MODELS, resolve_anthropic_model
    resolved = resolve_anthropic_model(model)
    if resolved is None:
        raise HTTPException(status_code=400, detail=f"Modelo no válido. Opciones: {ANTHROPIC_VALID_MODELS}")
    model = resolved

    orchestrator = get_orchestrator(request)
    status = orchestrator.llm.set_anthropic_model(model)

    return {
        "success": True,
        "model": model,
        "message": f"Modelo Anthropic cambiado a {model}"
    }


@router.post("/settings/deepseek-model")
async def change_deepseek_model(request: Request, model: str, session: dict = Depends(require_admin)):
    """Cambiar el modelo DeepSeek del chat N3 (flash / pro). No afecta a los clasificadores."""
    from core.model_catalog import DEEPSEEK_VALID_MODELS, resolve_deepseek_model
    resolved = resolve_deepseek_model(model)
    if resolved is None:
        raise HTTPException(status_code=400, detail=f"Modelo no válido. Opciones: {DEEPSEEK_VALID_MODELS}")

    orchestrator = get_orchestrator(request)
    orchestrator.llm.set_deepseek_model(resolved)

    return {
        "success": True,
        "model": resolved,
        "message": f"Modelo DeepSeek cambiado a {resolved}"
    }


# ── App launcher endpoints ─────────────────────────────────────────────────
#
# `POST /apps/open` y `POST /apps/focus` se BORRARON el 2026-10-01.
#
# Ejecutaban acciones a nivel SO — `AppLauncher.open_app(<string arbitrario>)`
# y `core.platform.focus_window()` — con `require_auth` como único control y
# SIN pasar por `core/action_risk_rules.classify_action`. El guardrail vive en
# el orchestrator, así que estos dos endpoints lo rodeaban por completo. El
# segundo era, además, el camino exacto que dejó la PC sin responder a los
# clicks (ver `core/platform.py` y el commit 891a44a).
#
# Quedaron huérfanos cuando el ownership de apps se movió al backend y se
# borraron las 329 líneas de routing de `src/chat.js` (ver
# `.claude/rules/app-routing-whitelists.md`, "Dueño de la ejecución"). Al
# borrarlos no tenían un solo llamador de producción: `rg "apps/open|apps/focus"`
# devolvía los dos decoradores y `tests/test_system_auth.py`, nada más.
#
# Si en algún momento se necesita abrir una app por HTTP, el camino correcto
# es un comando por el orchestrator, que es el que aplica el guardrail.
#
# `GET /apps/config` también se borró: exponía NATIVE_APPS / WEB_APP_URLS /
# SITE_KEYWORDS SIN auth, y su único consumidor era el `fetchAppsConfig()` del
# frontend que se eliminó en ese mismo refactor.


@router.get("/system/config")
async def get_system_config(request: Request, session: dict = Depends(require_admin)):
    """Retorna configuraciones del sistema (auto-cloud, telegram, etc.)."""
    import os

    auto_cloud = os.environ.get("AUTO_CLOUD", "true").lower() in ("1", "true", "yes", "on")
    telegram_enabled = os.environ.get("TELEGRAM_ENABLED", "true").lower() in ("1", "true", "yes", "on")

    return {
        "auto_cloud": auto_cloud,
        "telegram_enabled": telegram_enabled,
    }


@router.post("/system/config/toggle")
async def toggle_system_config(request: Request, body: SettingsUpdate, session: dict = Depends(require_admin)):
    """Activa/desactiva configuraciones del sistema (AUTO_CLOUD, TELEGRAM_ENABLED)."""
    import os

    valid_keys = {"AUTO_CLOUD", "TELEGRAM_ENABLED"}
    if body.key.upper() not in valid_keys:
        raise HTTPException(status_code=400, detail=f"Key no válida. Opciones: {', '.join(valid_keys)}")

    key = body.key.upper()
    value = body.value.lower()
    if value not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
        raise HTTPException(status_code=400, detail="Valor debe ser true/false")

    normalized = "true" if value in ("true", "1", "yes", "on") else "false"

    # Guardar en .env
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        env_content = env_path.read_text(encoding="utf-8")
        lines = env_content.splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
                new_lines.append(f"{key}={normalized}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={normalized}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Actualizar os.environ para efecto inmediato
    os.environ[key] = normalized

    return {"success": True, "key": key, "value": normalized}


# ── Reload / Restart endpoints ────────────────────────────────────────────────

@router.get("/system/needs-restart")
async def check_needs_restart(request: Request, session: dict = Depends(require_auth)):
    """Indica si hay archivos .py modificados desde el arranque del backend."""
    return {
        "needs_restart": _needs_restart,
        "changed_files": [Path(f).name for f in _changed_files],
    }


@router.post("/system/reload-config")
async def reload_config(request: Request, session: dict = Depends(require_admin)):
    """Re-lee .env y reinicializa clientes LLM + pool PG sin reiniciar el proceso."""
    env_path = _BACKEND_DIR / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    orch = request.app.state.orchestrator
    reloaded = ["env"]
    try:
        orch.llm._init_cloud_models()
        reloaded.append("llm")
    except Exception:
        pass
    try:
        orch.llm_fast._init_cloud_models()
        reloaded.append("llm_fast")
    except Exception:
        pass
    try:
        from modules.memory import pgvector_store as _pgvs
        _pgvs._pool = None
        reloaded.append("pg_pool")
    except Exception:
        pass

    return {"success": True, "reloaded": reloaded, "env_path": str(env_path)}


@router.post("/system/restart")
async def restart_backend(request: Request, session: dict = Depends(require_admin)):
    """Relanza el backend de forma segura: un proceso detached ESPERA a que el
    puerto se libere (el graceful shutdown de este backend puede tardar varios
    segundos por telegram/scheduler/whisper) antes de arrancar el nuevo, en vez
    de un sleep fijo que provocaba colisión de puerto (Errno 10048) y dejaba el
    backend caído. Preserva -X utf8 (evita UnicodeEncodeError en Windows) y el
    puerto real del request."""
    import asyncio
    import signal
    import subprocess
    import sys

    py = sys.executable
    script = str(_BACKEND_DIR / "web_app.py")
    backend_dir = str(_BACKEND_DIR)
    # Puerto real del request (preserva configs no-default); fallback 8000.
    port = (request.url.port or 8000)

    # Código del proceso relanzador: espera a que el puerto quede libre (con
    # tope de seguridad de 30s) + margen para TIME_WAIT, luego arranca el nuevo.
    watcher_code = (
        "import time, socket, subprocess\n"
        f"PORT = {port}\n"
        "def busy():\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(0.5)\n"
        "    try:\n"
        "        s.connect(('127.0.0.1', PORT)); return True\n"
        "    except OSError:\n"
        "        return False\n"
        "    finally:\n"
        "        s.close()\n"
        "deadline = time.time() + 30\n"
        "while busy() and time.time() < deadline:\n"
        "    time.sleep(0.5)\n"
        "time.sleep(1.0)\n"
        f"subprocess.Popen([r'{py}', '-X', 'utf8', r'{script}'], cwd=r'{backend_dir}')\n"
    )

    popen_kwargs: dict = {"cwd": backend_dir}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    subprocess.Popen([py, "-c", watcher_code], **popen_kwargs)

    asyncio.get_event_loop().call_later(
        0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)
    )
    return {"success": True,
            "message": "Reiniciando — el nuevo proceso espera a que el puerto se libere",
            "port": port, "pid": os.getpid()}
