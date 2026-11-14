"""
ChannelManager — Gestiona el lifecycle de todos los canales de comunicación.
"""
import asyncio
import os
from typing import Any

from channels.base import BaseChannel


class ChannelManager:
    """Registro y lifecycle de canales."""

    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        """Registra un canal."""
        self._channels[channel.channel_type] = channel

    async def auto_register(self) -> None:
        """
        Detecta canales disponibles por env vars y los registra automáticamente.
        """
        # Telegram
        if os.getenv("TELEGRAM_BOT_TOKEN"):
            try:
                from channels.telegram_channel import TelegramChannel
                channel = TelegramChannel(self.orchestrator)
                self.register(channel)
                print(f"[ChannelManager] Telegram registrado")
            except ImportError as e:
                print(f"[ChannelManager] Telegram no disponible: {e}")

        # WhatsApp
        if os.getenv("WHATSAPP_ENABLED", "").lower() == "true":
            try:
                from channels.whatsapp_channel import WhatsAppChannel
                channel = WhatsAppChannel(self.orchestrator)
                self.register(channel)
                print(f"[ChannelManager] WhatsApp registrado")
            except ImportError as e:
                print(f"[ChannelManager] WhatsApp no disponible: {e}")

    async def start_all(self) -> None:
        """Inicia todos los canales registrados."""
        for name, channel in self._channels.items():
            try:
                await channel.start()
                print(f"[ChannelManager] {name} iniciado")
            except Exception as e:
                print(f"[ChannelManager] Error iniciando {name}: {e}")

    async def stop_all(self) -> None:
        """Detiene todos los canales."""
        for name, channel in self._channels.items():
            try:
                await channel.stop()
            except Exception:
                pass

    def get_channel(self, name: str) -> BaseChannel | None:
        return self._channels.get(name)

    def get_status(self) -> dict:
        """Estado de todos los canales."""
        return {
            name: channel.get_status()
            for name, channel in self._channels.items()
        }

    def list_channels(self) -> list[str]:
        return list(self._channels.keys())

    async def broadcast_text(self, text: str) -> int:
        """Envía texto a todos los usuarios autorizados de todos los canales."""
        tasks = []
        for channel in self._channels.values():
            try:
                for uid in channel.get_authorized_ids():
                    tasks.append(channel.send(uid, text))
            except Exception:
                pass
        if not tasks:
            return 0
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, Exception))
