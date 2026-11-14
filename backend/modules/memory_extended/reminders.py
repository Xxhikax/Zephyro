# modules/memory_extended/reminders.py
"""
Sistema de recordatorios para Zephyro.
- Creación de recordatorios con tiempo natural ("en 2 horas", "mañana a las 10")
- Background check cada minuto
- Notificaciones via callback
- Persistencia en SQLite
"""
import asyncio
import json
import os
import re
import sqlite3
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Callable, Dict


class Reminder:
    """Representa un recordatorio."""
    def __init__(self, id: int, message: str, due_at: float, created_at: float,
                 user_role: str, completed: bool = False):
        self.id = id
        self.message = message
        self.due_at = due_at
        self.created_at = created_at
        self.user_role = user_role
        self.completed = completed

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "message": self.message,
            "due_at": self.due_at,
            "due_at_str": datetime.fromtimestamp(self.due_at).strftime("%d/%m/%Y %H:%M"),
            "created_at": self.created_at,
            "user_role": self.user_role,
            "completed": self.completed,
        }


class ReminderSystem:
    """
    Sistema completo de recordatorios.

    Uso:
        rs = ReminderSystem()
        rs.start(callback=lambda r: print(f"Recordatorio: {r.message}"))

        # Crear recordatorio
        id_, msg = rs.create_reminder("Llamar al médico", "en 2 horas")
        id_, msg = rs.create_reminder("Reunión de equipo", "mañana a las 9am")

        # Listar recordatorios
        print(rs.list_reminders())

        # Cancelar
        rs.cancel_reminder(id_)
    """

    DB_PATH = "./storage/reminders.db"
    CHECK_INTERVAL = 60  # Revisar cada minuto

    def __init__(self, db_path: str = None):
        self._db_path = db_path or self.DB_PATH
        self._running = False
        self._callback: Optional[Callable] = None
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    def _init_db(self):
        """Inicializa la base de datos SQLite."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    user_role TEXT DEFAULT 'admin',
                    completed INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_due_at ON reminders(due_at)")
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    # ------------------------------------------------------------------
    # Parse de tiempo natural
    # ------------------------------------------------------------------

    def _parse_time(self, time_str: str) -> Optional[float]:
        """
        Convierte expresiones de tiempo natural a timestamp Unix.

        Soporta:
        - "en 2 horas", "en 30 minutos", "en 1 día"
        - "mañana a las 10am", "mañana a las 10:30"
        - "a las 15:00", "a las 3pm"
        - Timestamps Unix directos
        """
        now = datetime.now()
        t = time_str.lower().strip()

        # "en X minutos/horas/días"
        match = re.search(r"en\s+(\d+)\s*(minuto|minutos|min|hora|horas|h|día|dias|dias|d)", t)
        if match:
            n = int(match.group(1))
            unit = match.group(2)
            if "min" in unit:
                return (now + timedelta(minutes=n)).timestamp()
            elif "h" in unit or "hora" in unit:
                return (now + timedelta(hours=n)).timestamp()
            elif "d" in unit or "día" in unit or "dia" in unit:
                return (now + timedelta(days=n)).timestamp()

        # "mañana a las HH:MM" o "mañana a las Hpm"
        if "mañana" in t or "manana" in t:
            tomorrow = now + timedelta(days=1)
            hour_match = re.search(r"(\d{1,2}):?(\d{0,2})\s*(am|pm)?", t)
            if hour_match:
                hour = int(hour_match.group(1))
                minute = int(hour_match.group(2)) if hour_match.group(2) else 0
                am_pm = hour_match.group(3)
                if am_pm == "pm" and hour < 12:
                    hour += 12
                elif am_pm == "am" and hour == 12:
                    hour = 0
                return tomorrow.replace(hour=hour, minute=minute, second=0).timestamp()
            else:
                return tomorrow.replace(hour=9, minute=0, second=0).timestamp()

        # "a las HH:MM" o "a las Hpm"
        match = re.search(r"a las?\s+(\d{1,2}):?(\d{0,2})\s*(am|pm)?", t)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.group(2) else 0
            am_pm = match.group(3)
            if am_pm == "pm" and hour < 12:
                hour += 12
            elif am_pm == "am" and hour == 12:
                hour = 0
            target = now.replace(hour=hour, minute=minute, second=0)
            # Si ya pasó la hora hoy, usar mañana
            if target.timestamp() <= now.timestamp():
                target += timedelta(days=1)
            return target.timestamp()

        # "esta tarde" (~18:00), "esta noche" (~21:00)
        if "esta tarde" in t:
            return now.replace(hour=18, minute=0, second=0).timestamp()
        if "esta noche" in t:
            return now.replace(hour=21, minute=0, second=0).timestamp()

        return None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_reminder(self, message: str, when_str: str,
                       user_role: str = "admin") -> tuple:
        """
        Crea un recordatorio.

        Args:
            message: Mensaje del recordatorio
            when_str: Expresión de tiempo ("en 2 horas", "mañana a las 9")
            user_role: Rol del usuario

        Returns:
            (reminder_id: Optional[int], feedback_message: str)
        """
        due_at = self._parse_time(when_str)

        if due_at is None:
            return None, (
                f"No entendí la expresión de tiempo: '{when_str}'\n"
                f"Prueba: 'en 2 horas', 'en 30 minutos', 'mañana a las 10am', 'a las 15:00'"
            )

        if due_at <= time.time():
            return None, f"La fecha/hora ya pasó. Especifica un tiempo futuro."

        with self._get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO reminders (message, due_at, created_at, user_role) VALUES (?, ?, ?, ?)",
                (message, due_at, time.time(), user_role)
            )
            reminder_id = cursor.lastrowid
            conn.commit()

        due_str = datetime.fromtimestamp(due_at).strftime("%d/%m/%Y a las %H:%M")
        return reminder_id, f"✅ Recordatorio #{reminder_id} creado para el {due_str}:\n'{message}'"

    def list_reminders(self, user_role: str = "admin", include_completed: bool = False) -> str:
        """Lista los recordatorios activos."""
        with self._get_conn() as conn:
            if include_completed:
                rows = conn.execute(
                    "SELECT * FROM reminders WHERE user_role=? ORDER BY due_at",
                    (user_role,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reminders WHERE user_role=? AND completed=0 ORDER BY due_at",
                    (user_role,)
                ).fetchall()

        if not rows:
            return "No tienes recordatorios activos. Crea uno con: 'Recuérdame [cosa] en [tiempo]'"

        now = time.time()
        lines = [f"**Recordatorios activos** ({len(rows)}):\n"]

        for row in rows:
            reminder_id, message, due_at, created_at, role, completed = row
            due_dt = datetime.fromtimestamp(due_at)
            remaining = due_at - now

            if remaining > 0:
                if remaining < 3600:
                    time_str = f"en {int(remaining/60)} min"
                elif remaining < 86400:
                    time_str = f"en {int(remaining/3600)}h"
                else:
                    time_str = due_dt.strftime("%d/%m a las %H:%M")
            else:
                time_str = "VENCIDO"

            status = "✅" if completed else ("⏰" if remaining <= 0 else "🔔")
            lines.append(f"{status} #{reminder_id}: {message}")
            lines.append(f"   📅 {due_dt.strftime('%d/%m/%Y %H:%M')} ({time_str})")

        return "\n".join(lines)

    def cancel_reminder(self, reminder_id: int) -> str:
        """Cancela un recordatorio."""
        with self._get_conn() as conn:
            result = conn.execute(
                "UPDATE reminders SET completed=1 WHERE id=?",
                (reminder_id,)
            )
            conn.commit()

        if result.rowcount > 0:
            return f"✅ Recordatorio #{reminder_id} cancelado."
        return f"No encontré el recordatorio #{reminder_id}."

    def _mark_completed(self, reminder_id: int):
        """Marca un recordatorio como completado."""
        with self._get_conn() as conn:
            conn.execute("UPDATE reminders SET completed=1 WHERE id=?", (reminder_id,))
            conn.commit()

    # ------------------------------------------------------------------
    # Background check
    # ------------------------------------------------------------------

    def _check_due_reminders(self) -> List[Reminder]:
        """Busca recordatorios vencidos no completados."""
        now = time.time()
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, message, due_at, created_at, user_role, completed "
                "FROM reminders WHERE due_at <= ? AND completed = 0",
                (now,)
            ).fetchall()

        due = []
        for row in rows:
            r = Reminder(*row)
            self._mark_completed(r.id)
            due.append(r)

        return due

    def get_reminders_due_before(self, before_ts: float) -> List[Reminder]:
        """Retorna recordatorios no completados con due_at <= before_ts.
        A diferencia de _check_due_reminders, NO los marca como completados.
        Útil para notificaciones proactivas (avisar antes de que venzan)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, message, due_at, created_at, user_role, completed "
                "FROM reminders WHERE due_at <= ? AND completed = 0 "
                "ORDER BY due_at",
                (before_ts,),
            ).fetchall()
        return [Reminder(*row) for row in rows]

    async def _check_loop(self):
        """Loop de verificación de recordatorios."""
        print(f"[ReminderSystem] Monitor iniciado.")
        while self._running:
            try:
                due = self._check_due_reminders()
                for reminder in due:
                    print(f"[ReminderSystem] Recordatorio vencido: #{reminder.id} - {reminder.message}")
                    if self._callback:
                        try:
                            self._callback(reminder)
                        except Exception as e:
                            print(f"[ReminderSystem] Error en callback: {e}")
            except Exception as e:
                print(f"[ReminderSystem] Error en check loop: {e}")

            await asyncio.sleep(self.CHECK_INTERVAL)

    def start(self, callback: Optional[Callable] = None):
        """
        Inicia el monitor de recordatorios.

        Args:
            callback: Función llamada con un Reminder cuando vence
        """
        if self._running:
            return

        self._running = True
        self._callback = callback

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._check_loop())
            else:
                threading.Thread(
                    target=lambda: asyncio.run(self._check_loop()),
                    daemon=True
                ).start()
        except Exception as e:
            print(f"[ReminderSystem] Error iniciando: {e}")

    def stop(self):
        """Detiene el monitor."""
        self._running = False
