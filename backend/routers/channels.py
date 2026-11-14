"""
Router para gestión de canales de comunicación (Telegram, WhatsApp, etc.).
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["channels"])


@router.get("/channels")
async def list_channels(request: Request):
    """Lista canales activos con su estado."""
    cm = getattr(request.app.state, "channel_manager", None)
    if not cm:
        return JSONResponse({"channels": {}, "error": "ChannelManager not initialized"})
    return {"channels": cm.get_status(), "available": cm.list_channels()}


@router.post("/channels/{name}/start")
async def start_channel(name: str, request: Request):
    """Inicia un canal específico."""
    cm = getattr(request.app.state, "channel_manager", None)
    if not cm:
        return JSONResponse({"error": "ChannelManager not initialized"}, status_code=500)
    channel = cm.get_channel(name)
    if not channel:
        return JSONResponse({"error": f"Canal '{name}' no registrado"}, status_code=404)
    try:
        await channel.start()
        return {"status": "started", "channel": name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/channels/{name}/stop")
async def stop_channel(name: str, request: Request):
    """Detiene un canal específico."""
    cm = getattr(request.app.state, "channel_manager", None)
    if not cm:
        return JSONResponse({"error": "ChannelManager not initialized"}, status_code=500)
    channel = cm.get_channel(name)
    if not channel:
        return JSONResponse({"error": f"Canal '{name}' no registrado"}, status_code=404)
    try:
        await channel.stop()
        return {"status": "stopped", "channel": name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
