"""Vision router — image analysis, screen capture, and continuous vision endpoints."""
import asyncio
import json as _json
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

router = APIRouter()

from core.auth_middleware import require_auth
from core.paths import UPLOAD_DIR


def get_orchestrator(request: Request):
    return request.app.state.orchestrator


@router.post("/vision/analyze")
async def analyze_image(
    request: Request,
    session: dict = Depends(require_auth),
    prompt: Optional[str] = Form("Analiza esta imagen en detalle"),
    use_cloud: Optional[bool] = Form(True),
    file: UploadFile = File(...)
):
    """Analizar imagen subida"""
    if use_cloud and session.get("role") == "guest":
        use_cloud = False

    temp_file = None

    try:
        if not file.content_type or not file.content_type.startswith('image/'):
            return {
                "success": False,
                "error": f"Tipo de archivo no soportado: {file.content_type}. Solo se aceptan imágenes."
            }

        file_extension = Path(file.filename).suffix
        if not file_extension:
            ext_map = {
                'image/jpeg': '.jpg',
                'image/png': '.png',
                'image/gif': '.gif',
                'image/webp': '.webp'
            }
            file_extension = ext_map.get(file.content_type, '.png')

        temp_file = UPLOAD_DIR / f"temp_image_{secrets.token_hex(8)}{file_extension}"

        with temp_file.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        if not temp_file.exists() or temp_file.stat().st_size == 0:
            return {
                "success": False,
                "error": "Error al guardar la imagen. El archivo está vacío."
            }

        orchestrator = get_orchestrator(request)
        start_time = time.perf_counter()
        result = orchestrator.vision_analyze.analyze(str(temp_file), prompt, use_cloud=use_cloud)
        elapsed_time = time.perf_counter() - start_time

        if result.startswith("[") and "Error" in result:
            return {
                "success": False,
                "error": result,
                "elapsed_time": round(elapsed_time, 2)
            }

        temp_file.unlink()

        return {
            "success": True,
            "analysis": result,
            "elapsed_time": round(elapsed_time, 2),
            "used_cloud": use_cloud,
            "filename": file.filename
        }

    except Exception as e:
        if temp_file and temp_file.exists():
            temp_file.unlink()
        return {
            "success": False,
            "error": f"Error al procesar imagen: {str(e)}"
        }


@router.post("/vision/screen")
async def analyze_screen(
    request: Request,
    session: dict = Depends(require_auth),
    prompt: Optional[str] = Form("Describe lo que ves en esta captura de pantalla. Sé conciso: qué aplicación está abierta, qué está haciendo el usuario, y cualquier detalle relevante."),
):
    """Capture and analyze the current screen"""
    try:
        orchestrator = get_orchestrator(request)
        start_time = time.perf_counter()
        image_path = orchestrator.vision_capture.capture()
        result = orchestrator.vision_analyze.analyze(image_path, prompt, use_cloud=True, provider="anthropic")
        elapsed_time = time.perf_counter() - start_time

        # Clean up screenshot
        try:
            Path(image_path).unlink()
        except Exception:
            pass

        if result.startswith("[") and "Error" in result:
            return {"success": False, "error": result, "elapsed_time": round(elapsed_time, 2)}

        return {
            "success": True,
            "analysis": result,
            "elapsed_time": round(elapsed_time, 2),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/vision/screen-query")
async def screen_query(
    request: Request,
    session: dict = Depends(require_auth),
    question: str = Form("Que ves en la pantalla?"),
):
    """Captura la pantalla y responde una pregunta del usuario sobre lo que ve."""
    try:
        orchestrator = get_orchestrator(request)
        start_time = time.perf_counter()
        image_path = orchestrator.vision_capture.capture()

        prompt = (
            f"El usuario pregunta: \"{question}\"\n\n"
            "Esta es una captura de su pantalla. Responde de forma concisa y directa "
            "en espanol. Si ves la interfaz de Zephyro (chat, Tauri, JARVIS), "
            "ignorala y concentrate en el contenido del resto de la pantalla.\n\n"
            "Responde solo lo que el usuario pregunto. Maximo 4 oraciones."
        )
        result = orchestrator.vision_analyze.analyze(image_path, prompt, use_cloud=True)
        elapsed_time = time.perf_counter() - start_time

        try:
            Path(image_path).unlink()
        except Exception:
            pass

        if result.startswith("[") and "Error" in result:
            return {"success": False, "error": result, "elapsed_time": round(elapsed_time, 2)}

        return {
            "success": True,
            "answer": result,
            "question": question,
            "elapsed_time": round(elapsed_time, 2),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Continuous vision state ──────────────────────────────────────────────────
_continuous_vision = {
    "active": False,
    "interval": 10,  # seconds
    "context": {
        "active_app": "",
        "user_activity": "",
        "context_hint": "",
        "last_updated": "",
        "last_updated_ts": 0.0,
    },
    "task": None,
}


def get_vision_context() -> Optional[str]:
    """Returns a formatted vision context string if watching is active, else None."""
    if not _continuous_vision["active"]:
        return None
    ctx = _continuous_vision["context"]
    active_app = ctx.get("active_app", "")
    activity = ctx.get("user_activity", "") or ctx.get("summary", "")
    hint = ctx.get("context_hint", "")
    if not active_app and not activity:
        return None
    parts = []
    if active_app:
        parts.append(f"App activa: {active_app}.")
    if activity:
        parts.append(f"Actividad: {activity}.")
    if hint:
        parts.append(f"Relevancia: {hint}.")
    return " ".join(parts)


@router.post("/vision/continuous/start")
async def start_continuous_vision(
    request: Request,
    session: dict = Depends(require_auth),
    interval: int = Form(10),
):
    """Start continuous screen observation mode"""
    new_interval = max(3, min(interval, 60))
    if _continuous_vision["active"]:
        _continuous_vision["interval"] = new_interval
        return {"success": True, "message": "Intervalo actualizado", "context": _continuous_vision["context"]}

    _continuous_vision["active"] = True
    _continuous_vision["interval"] = new_interval

    orchestrator = get_orchestrator(request)

    async def vision_loop():
        while _continuous_vision["active"]:
            try:
                image_path = orchestrator.vision_capture.capture()
                prompt = (
                    "Analiza esta captura de pantalla. Responde SOLO en JSON con este formato exacto:\n"
                    '{"active_app": "nombre de la aplicación o ventana principal visible", '
                    '"user_activity": "descripción concreta de qué está haciendo el usuario ahora mismo (1 oración)", '
                    '"context_hint": "en qué podría ayudar el asistente dado lo que ve (1 oración breve, o vacío si no aplica)"}'
                )
                result = orchestrator.vision_analyze.analyze(
                    image_path, prompt, use_cloud=True, provider="anthropic"
                )
                try:
                    Path(image_path).unlink()
                except Exception:
                    pass

                # Parse JSON response from Haiku
                try:
                    json_match = result
                    if '{' in result:
                        json_match = result[result.index('{'):result.rindex('}') + 1]
                    parsed = _json.loads(json_match)
                    import time as _time_mod
                    _ts_now = _time_mod.time()
                    _continuous_vision["context"] = {
                        "active_app": parsed.get("active_app", ""),
                        "user_activity": parsed.get("user_activity", ""),
                        "context_hint": parsed.get("context_hint", ""),
                        "last_updated": datetime.now().strftime("%H:%M:%S"),
                        "last_updated_ts": _ts_now,
                    }
                except (_json.JSONDecodeError, ValueError):
                    import time as _time_mod
                    _continuous_vision["context"]["user_activity"] = result[:200]
                    _continuous_vision["context"]["last_updated"] = datetime.now().strftime("%H:%M:%S")
                    _continuous_vision["context"]["last_updated_ts"] = _time_mod.time()

            except Exception as e:
                print(f"[Vision] Continuous error: {e}")

            await asyncio.sleep(_continuous_vision["interval"])

    _continuous_vision["task"] = asyncio.create_task(vision_loop())
    return {"success": True, "message": f"Observación continua iniciada (cada {_continuous_vision['interval']}s)"}


@router.post("/vision/continuous/stop")
async def stop_continuous_vision(
    session: dict = Depends(require_auth),
):
    """Stop continuous screen observation"""
    _continuous_vision["active"] = False
    if _continuous_vision["task"]:
        _continuous_vision["task"].cancel()
        _continuous_vision["task"] = None
    return {"success": True, "message": "Observación continua detenida"}


@router.get("/vision/continuous/status")
async def continuous_vision_status(session: dict = Depends(require_auth)):
    """Get current continuous vision state"""
    return {
        "active": _continuous_vision["active"],
        "interval": _continuous_vision["interval"],
        "context": _continuous_vision["context"],
    }
