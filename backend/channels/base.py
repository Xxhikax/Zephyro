"""
BaseChannel — Abstracción para canales de comunicación de Zephyro.

Cada canal (WebSocket, Telegram, WhatsApp) traduce mensajes de su plataforma
a ChannelMessage, los pasa al orchestrator, y envía la respuesta de vuelta.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ChannelMessage:
    """Mensaje normalizado de cualquier canal."""
    text: str
    channel_type: str          # "websocket" | "telegram" | "whatsapp"
    channel_user_id: str       # ID único del usuario en el canal
    session_id: str = ""       # Zephyro session ID (mapeado)
    user_name: str = ""        # Nombre del usuario si disponible
    reply_to: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class BaseChannel(ABC):
    """Clase base para adaptadores de canal."""

    channel_type: str = "unknown"

    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @abstractmethod
    async def start(self) -> None:
        """Inicia el canal (conecta, registra handlers)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Detiene el canal limpiamente."""
        ...

    @abstractmethod
    async def send(self, channel_user_id: str, text: str) -> None:
        """Envía un mensaje a un usuario del canal."""
        ...

    async def process_message(self, msg: ChannelMessage) -> str:
        """
        Procesa un mensaje recibido: lo pasa al orchestrator y retorna respuesta.
        Este método es compartido por todos los canales.
        """
        from core.request_context import RequestContext

        ctx = RequestContext(
            user_role="admin",  # Por defecto admin; canales pueden sobreescribir
            user_name=msg.user_name or None,
            authenticated=True,
            session_id=msg.session_id or msg.channel_user_id,
            channel_type=msg.channel_type,
            channel_user_id=msg.channel_user_id,
        )

        try:
            response = await self.orchestrator.handle_input(msg.text, ctx=ctx)
            return response or "Sin respuesta."
        except Exception as e:
            return f"Error procesando mensaje: {e}"

    def get_status(self) -> dict:
        """Retorna estado del canal."""
        return {
            "type": self.channel_type,
            "running": self._running,
        }

    def get_authorized_ids(self) -> list:
        """Retorna IDs de usuarios autorizados en este canal."""
        return []
