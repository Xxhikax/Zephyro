"""Sistema de gestión de horarios y agenda para Zephyro.

Walking skeleton: persistencia SQLite + CRUD básico.
Recurrencia, pending_changes y sync GCal vendrán en fases posteriores.
"""
from modules.schedule.service import ScheduleService, ScheduleEvent

__all__ = ["ScheduleService", "ScheduleEvent"]
