"""
RequestContext — per-request state passed as a parameter instead of mutating Orchestrator.
Eliminates race conditions from concurrent requests.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


@dataclass
class RequestContext:
    user_role: str
    user_name: Optional[str]
    authenticated: bool
    session_id: str
    force_mode: Optional[str] = None
    reasoning_mode: bool = False
    speak: bool = False
    force_remember: bool = False
    channel_type: str = "websocket"
    channel_user_id: Optional[str] = None
    device_type: str = "desktop"
    request_id: Optional[str] = None
    status_callback: Optional[Callable[[dict], Awaitable[None]]] = None
    vision_context: Optional[str] = None
    conversation_history: list = field(default_factory=list)
    # Per-request component timings (seconds). Populated by the orchestrator
    # via _print_timing_stats. Read by the WS handler to expose llm_ms etc.
    # to the frontend HUD without inventing fake numbers.
    timings: dict = field(default_factory=dict)

    # Fase 1 streaming-tts-backend fields — see sdd/streaming-tts-backend/design
    # Cuando stream_tts=True y tts_chunk_callback está seteado, el orchestrator
    # usa el path de streaming WS en vez del push único tts_audio.
    stream_tts: bool = False
    tts_chunk_callback: Optional[Callable[[str, str, int, bool], Awaitable[None]]] = None
    # Set by the streaming path when generation finished but sentences are still being
    # synthesized: the WS handler sends the `response` text first, then awaits this task
    # before the terminal tts_chunk (so on-screen text doesn't wait for all the audio).
    tts_drain: Optional[asyncio.Task] = None

    async def emit_status(self, action: str, message: str) -> None:
        cb = self.status_callback
        if cb is None:
            return
        try:
            await cb({"action": action, "message": message, "request_id": self.request_id})
        except Exception:
            pass

    async def emit_status_deferred(
        self, action: str, message: str, after_ms: int = 2500
    ) -> Optional[asyncio.Task]:
        """
        Agenda un emit_status que solo dispara si pasan after_ms sin que se cancele.
        Devuelve el asyncio.Task — el caller debe llamar task.cancel() cuando la
        respuesta llegue, para evitar disparar el filler si fue rápido.
        """
        cb = self.status_callback
        if cb is None:
            return None

        request_id = self.request_id

        async def _delayed():
            try:
                await asyncio.sleep(after_ms / 1000)
                await cb({"action": action, "message": message, "request_id": request_id})
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        return asyncio.create_task(_delayed())
