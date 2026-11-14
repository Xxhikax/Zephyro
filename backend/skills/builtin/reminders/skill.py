"""Reminders skill — wrapper sobre modules/memory_extended/reminders.py"""
import re
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill


class RemindersSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._system = None
        self._reminder_callback = None

    def _get_system(self):
        if self._system is None:
            from modules.memory_extended.reminders import ReminderSystem
            self._system = ReminderSystem()
            if self._reminder_callback:
                self._system.start(callback=self._reminder_callback)
        return self._system

    def set_reminder_callback(self, callback):
        """Permite al orchestrator inyectar el callback de reminder due."""
        self._reminder_callback = callback
        if self._system:
            self._system.start(callback=callback)

    @property
    def system(self):
        """Acceso directo al ReminderSystem (para orchestrator)."""
        return self._system

    def matches_command(self, user_input: str) -> bool:
        lower = user_input.lower().strip()
        # "recuérdame " necesita match por startswith (tiene espacio)
        if lower.startswith("recuérdame ") or lower.startswith("recuerdame "):
            return True
        if lower in ("/recordatorios", "/reminders", "lista recordatorios", "mis recordatorios"):
            return True
        return False

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        lower = user_input.lower().strip()

        # Listar recordatorios
        if lower in ("/recordatorios", "/reminders", "lista recordatorios", "mis recordatorios"):
            return self._handle_list(ctx)

        # Crear recordatorio
        return self._handle_create(user_input, ctx)

    def _handle_create(self, user_input: str, ctx: RequestContext) -> str:
        try:
            system = self._get_system()
        except Exception as e:
            return f"Sistema de recordatorios no disponible: {e}"

        text = re.sub(r'^recuérdame\s+|^recuerdame\s+', '', user_input, flags=re.IGNORECASE).strip()

        time_separators = [
            r'\s+en\s+(\d+\s*(?:hora|horas|minuto|minutos|día|días|dia|dias)[^$]*)',
            r'\s+(mañana\s+a\s+las?\s+\S+)',
            r'\s+(a\s+las?\s+\d+[:\d]*\s*(?:am|pm)?)',
            r'\s+(esta\s+(?:tarde|noche))',
        ]

        message = text
        when_str = ""

        for pattern in time_separators:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                when_str = match.group(1).strip()
                message = text[:match.start()].strip()
                break

        if not when_str:
            return (
                "No entendí cuándo quieres que te recuerde.\n"
                "Prueba: 'Recuérdame [cosa] en 2 horas' o 'Recuérdame [cosa] mañana a las 10am'"
            )

        if not message:
            return "No entendí qué quieres que te recuerde. Dime el mensaje."

        user_role = ctx.user_role or "admin"
        reminder_id, feedback = system.create_reminder(message, when_str, user_role)
        return feedback

    def _handle_list(self, ctx: RequestContext) -> str:
        try:
            system = self._get_system()
        except Exception as e:
            return f"Sistema de recordatorios no disponible: {e}"

        user_role = ctx.user_role or "admin"
        return system.list_reminders(user_role)
