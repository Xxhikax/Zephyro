"""ScheduleService — gestión de eventos de agenda con persistencia SQLite."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


DEFAULT_CATEGORIES = (
    "university",
    "uni_exam",
    "uni_assignment",
    "course_study",
    "personal_project",
    "client_project",
    "exercise",
    "fitness",
    "other",
)


# Callback registry para hooks externos (ej. FitnessService reaccionando a eventos nuevos)
_event_callbacks: dict[str, list[Callable]] = {
    "created": [],
    "updated": [],
    "deleted": [],
}
_callbacks_lock = threading.Lock()


def register_event_callback(event_type: str, func: Callable) -> None:
    """Registra un callback para 'created' | 'updated' | 'deleted'.
    Los callbacks se disparan async (fire-and-forget) tras la operación.
    """
    if event_type not in _event_callbacks:
        raise ValueError(f"event_type debe ser created/updated/deleted, no {event_type!r}")
    with _callbacks_lock:
        if func not in _event_callbacks[event_type]:
            _event_callbacks[event_type].append(func)


def _fire_callbacks(event_type: str, payload) -> None:
    """Dispara los callbacks registrados sin bloquear. Tolera ausencia de event loop."""
    cbs = list(_event_callbacks.get(event_type, []))
    if not cbs:
        return
    try:
        loop = asyncio.get_event_loop()
        running = loop.is_running()
    except RuntimeError:
        running = False
    for cb in cbs:
        try:
            if running and asyncio.iscoroutinefunction(cb):
                asyncio.ensure_future(cb(payload))
            elif asyncio.iscoroutinefunction(cb):
                threading.Thread(target=lambda: asyncio.run(cb(payload)), daemon=True).start()
            else:
                threading.Thread(target=cb, args=(payload,), daemon=True).start()
        except Exception as exc:
            logger.debug("[schedule.callback] %s falló: %s", event_type, exc)


MAX_EVENT_DURATION_DAYS = 180
MAX_PAST_START_HOURS = 24


@dataclass
class ScheduleEvent:
    id: int
    user_id: str
    title: str
    category: str
    start_ts_utc: float
    end_ts_utc: float
    notes: str
    created_at: float
    status: str = "pending"
    completed_at: Optional[float] = None
    rescheduled_from: Optional[int] = None
    gcal_id: Optional[str] = None
    notified_at: Optional[float] = None
    recurrence: Optional[str] = None
    parent_id: Optional[int] = None
    reminder_profile: Optional[str] = None  # 'default' | 'fitness' | ... ; None == 'default'

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start_iso"] = datetime.fromtimestamp(self.start_ts_utc, tz=timezone.utc).isoformat()
        d["end_iso"] = datetime.fromtimestamp(self.end_ts_utc, tz=timezone.utc).isoformat()
        return d


class ScheduleService:
    """Persistencia y CRUD para eventos de agenda."""

    DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "storage" / "schedule.db")

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or self.DB_PATH
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    start_ts_utc REAL NOT NULL,
                    end_ts_utc REAL NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    status TEXT DEFAULT 'pending',
                    completed_at REAL,
                    rescheduled_from INTEGER,
                    gcal_id TEXT,
                    notified_at REAL,
                    recurrence TEXT,
                    parent_id INTEGER
                )
                """
            )
            # Migrations: add columns when upgrading from older schemas
            _migrations = [
                "ALTER TABLE schedule_events ADD COLUMN status TEXT DEFAULT 'pending'",
                "ALTER TABLE schedule_events ADD COLUMN completed_at REAL",
                "ALTER TABLE schedule_events ADD COLUMN rescheduled_from INTEGER",
                "ALTER TABLE schedule_events ADD COLUMN gcal_id TEXT",
                "ALTER TABLE schedule_events ADD COLUMN notified_at REAL",
                "ALTER TABLE schedule_events ADD COLUMN recurrence TEXT",
                "ALTER TABLE schedule_events ADD COLUMN parent_id INTEGER",
                "ALTER TABLE schedule_events ADD COLUMN reminder_profile TEXT",
            ]
            for stmt in _migrations:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_start "
                "ON schedule_events(user_id, start_ts_utc)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_status "
                "ON schedule_events(user_id, status)"
            )
            conn.commit()

    def create_event(
        self,
        user_id: str,
        title: str,
        category: str,
        start_ts_utc: float,
        end_ts_utc: float,
        notes: str = "",
        recurrence: Optional[str] = None,
        parent_id: Optional[int] = None,
        reminder_profile: Optional[str] = None,
    ) -> ScheduleEvent:
        if category not in DEFAULT_CATEGORIES:
            category = "other"
        if end_ts_utc <= start_ts_utc:
            end_ts_utc = start_ts_utc + 3600

        duration_days = (end_ts_utc - start_ts_utc) / 86400
        if duration_days > MAX_EVENT_DURATION_DAYS:
            raise ValueError(f"La duración del evento no puede superar {MAX_EVENT_DURATION_DAYS} días")

        now = time.time()
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "INSERT INTO schedule_events "
                "(user_id, title, category, start_ts_utc, end_ts_utc, notes, created_at, status, recurrence, parent_id, reminder_profile) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (user_id, title, category, start_ts_utc, end_ts_utc, notes, now, recurrence, parent_id, reminder_profile),
            )
            event_id = cur.lastrowid
            conn.commit()

        event = ScheduleEvent(
            id=event_id,
            user_id=user_id,
            title=title,
            category=category,
            start_ts_utc=start_ts_utc,
            end_ts_utc=end_ts_utc,
            notes=notes,
            created_at=now,
            recurrence=recurrence,
            parent_id=parent_id,
            reminder_profile=reminder_profile,
        )
        self._sync_gcal_create(event)
        _fire_callbacks("created", event)
        return event

    def _sync_gcal_create(self, event: ScheduleEvent) -> None:
        if os.getenv("GOOGLE_CALENDAR_SYNC", "false").lower() != "true":
            return
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(self._gcal_push_async(event))
        except RuntimeError:
            pass

    async def _gcal_push_async(self, event: ScheduleEvent) -> None:
        try:
            from modules.integrations.google_calendar import GoogleCalendarClient
            gcal = GoogleCalendarClient()
            if not gcal.is_configured():
                return
            gcal_id = await gcal.push_event({
                "title": event.title,
                "start_ts_utc": event.start_ts_utc,
                "end_ts_utc": event.end_ts_utc,
                "notes": event.notes,
                "category": event.category,
            })
            if gcal_id:
                with self._lock, sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "UPDATE schedule_events SET gcal_id=? WHERE id=?",
                        (gcal_id, event.id),
                    )
                    conn.commit()
        except Exception as exc:
            logger.warning("GCal push failed for event %s: %s", event.id, exc)

    async def _gcal_update_async(self, event: ScheduleEvent) -> None:
        if not event.gcal_id:
            return
        try:
            from modules.integrations.google_calendar import GoogleCalendarClient
            gcal = GoogleCalendarClient()
            if not gcal.is_configured():
                return
            await gcal.update_event(event.gcal_id, {
                "title": event.title,
                "start_ts_utc": event.start_ts_utc,
                "end_ts_utc": event.end_ts_utc,
                "notes": event.notes,
                "category": event.category,
            })
        except Exception as exc:
            logger.warning("GCal update failed for event %s: %s", event.id, exc)

    async def _gcal_delete_async(self, gcal_id: str) -> None:
        if not gcal_id:
            return
        try:
            from modules.integrations.google_calendar import GoogleCalendarClient
            gcal = GoogleCalendarClient()
            if not gcal.is_configured():
                return
            await gcal.delete_event(gcal_id)
        except Exception as exc:
            logger.warning("GCal delete failed for gcal_id %s: %s", gcal_id, exc)

    def _sync_gcal_update(self, event: ScheduleEvent) -> None:
        if os.getenv("GOOGLE_CALENDAR_SYNC", "false").lower() != "true":
            return
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(self._gcal_update_async(event))
        except RuntimeError:
            pass

    def _sync_gcal_delete(self, gcal_id: str) -> None:
        if os.getenv("GOOGLE_CALENDAR_SYNC", "false").lower() != "true" or not gcal_id:
            return
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(self._gcal_delete_async(gcal_id))
        except RuntimeError:
            pass

    def _index_in_kg(self, event: ScheduleEvent) -> None:
        """Hook no bloqueante: indexa el evento en el knowledge graph."""
        try:
            from modules.memory_extended.knowledge_graph import KnowledgeGraph
            from datetime import datetime as _dt, timezone as _tz
            kg = KnowledgeGraph()
            kg.add_entity(
                entity_type="schedule_event",
                entity_id=f"evt_{event.id}",
                properties={
                    "title": event.title,
                    "category": event.category,
                    "day": _dt.fromtimestamp(event.start_ts_utc, tz=_tz.utc).strftime("%A"),
                    "start_iso": _dt.fromtimestamp(event.start_ts_utc, tz=_tz.utc).isoformat(),
                    "end_iso": _dt.fromtimestamp(event.end_ts_utc, tz=_tz.utc).isoformat(),
                    "status": event.status,
                    "notes": event.notes[:200] if event.notes else "",
                },
            )
        except Exception as exc:
            logger.debug("KG indexing failed for event %s: %s", event.id, exc)

    def get_events_in_range(
        self,
        user_id: str,
        from_ts_utc: float,
        to_ts_utc: float,
    ) -> list[ScheduleEvent]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, user_id, title, category, start_ts_utc, end_ts_utc, "
                "notes, created_at, "
                "COALESCE(status, 'pending'), completed_at, rescheduled_from, "
                "COALESCE(gcal_id, ''), notified_at, recurrence, parent_id, reminder_profile "
                "FROM schedule_events "
                "WHERE user_id=? AND start_ts_utc>=? AND start_ts_utc<? "
                "ORDER BY start_ts_utc",
                (user_id, from_ts_utc, to_ts_utc),
            ).fetchall()
        return [ScheduleEvent(*row) for row in rows]

    def update_event(
        self,
        event_id: int,
        title: Optional[str] = None,
        category: Optional[str] = None,
        start_ts_utc: Optional[float] = None,
        end_ts_utc: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> Optional[ScheduleEvent]:
        """Actualiza campos individuales de un evento. Solo modifica los provistos."""
        if category is not None and category not in DEFAULT_CATEGORIES:
            category = "other"
        if start_ts_utc is not None and end_ts_utc is not None and end_ts_utc <= start_ts_utc:
            end_ts_utc = start_ts_utc + 3600

        sets = []
        values = []
        if title is not None:
            sets.append("title=?")
            values.append(title)
        if category is not None:
            sets.append("category=?")
            values.append(category)
        if start_ts_utc is not None:
            sets.append("start_ts_utc=?")
            values.append(start_ts_utc)
        if end_ts_utc is not None:
            sets.append("end_ts_utc=?")
            values.append(end_ts_utc)
        if notes is not None:
            sets.append("notes=?")
            values.append(notes)

        if not sets:
            return None

        values.append(event_id)
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                f"UPDATE schedule_events SET {', '.join(sets)} WHERE id=?",
                values,
            )
            conn.commit()

        event = self.get_event_by_id(event_id)
        if event:
            self._sync_gcal_update(event)
            _fire_callbacks("updated", event)
        return event

    def get_event_by_id(self, event_id: int) -> Optional[ScheduleEvent]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, user_id, title, category, start_ts_utc, end_ts_utc, "
                "notes, created_at, "
                "COALESCE(status, 'pending'), completed_at, rescheduled_from, "
                "COALESCE(gcal_id, ''), notified_at, recurrence, parent_id, reminder_profile "
                "FROM schedule_events WHERE id=?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return ScheduleEvent(*row)

    def check_overlap(
        self,
        user_id: str,
        start_ts_utc: float,
        end_ts_utc: float,
        exclude_id: Optional[int] = None,
    ) -> list[ScheduleEvent]:
        """Retorna eventos del usuario que se solapan con el rango dado."""
        with sqlite3.connect(self._db_path) as conn:
            query = (
                "SELECT id, user_id, title, category, start_ts_utc, end_ts_utc, "
                "notes, created_at, "
                "COALESCE(status, 'pending'), completed_at, rescheduled_from, "
                "COALESCE(gcal_id, ''), notified_at, recurrence, parent_id, reminder_profile "
                "FROM schedule_events "
                "WHERE user_id=? AND status != 'cancelled' "
                "AND start_ts_utc < ? AND end_ts_utc > ?"
            )
            params: list = [user_id, end_ts_utc, start_ts_utc]
            if exclude_id is not None:
                query += " AND id != ?"
                params.append(exclude_id)
            rows = conn.execute(query, params).fetchall()
        return [ScheduleEvent(*row) for row in rows]

    def delete_event(self, event_id: int) -> bool:
        event = self.get_event_by_id(event_id)
        gcal_id = event.gcal_id if event else None
        with self._lock, sqlite3.connect(self._db_path) as conn:
            result = conn.execute(
                "DELETE FROM schedule_events WHERE id=?", (event_id,)
            )
            conn.commit()
        if result.rowcount > 0:
            if gcal_id:
                self._sync_gcal_delete(gcal_id)
            if event:
                _fire_callbacks("deleted", event)
        return result.rowcount > 0

    def mark_completed(self, event_id: int, completed: bool = True) -> bool:
        status = "completed" if completed else "pending"
        ts = time.time() if completed else None
        with self._lock, sqlite3.connect(self._db_path) as conn:
            result = conn.execute(
                "UPDATE schedule_events SET status=?, completed_at=? WHERE id=?",
                (status, ts, event_id),
            )
            conn.commit()
        return result.rowcount > 0

    def mark_missed(self, event_id: int) -> bool:
        """Marca un evento como 'missed' (T+30 sin confirmación)."""
        with self._lock, sqlite3.connect(self._db_path) as conn:
            result = conn.execute(
                "UPDATE schedule_events SET status='missed' WHERE id=? AND status='pending'",
                (event_id,),
            )
            conn.commit()
        return result.rowcount > 0

    def mark_cancelled(self, event_id: int) -> bool:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            result = conn.execute(
                "UPDATE schedule_events SET status='cancelled' WHERE id=?",
                (event_id,),
            )
            conn.commit()
        return result.rowcount > 0

    def find_free_slots(
        self,
        user_id: str,
        date_str: str,
        duration_minutes: int = 60,
        start_hour: int = 7,
        end_hour: int = 22,
    ) -> list[dict]:
        """Busca ventanas libres en un día para reprogramar eventos."""
        from datetime import datetime as _dt, time as _dtime, timedelta as _td, timezone as _tz

        try:
            target_date = _dt.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return []

        day_start = _dt.combine(target_date, _dtime(start_hour, 0), tzinfo=_tz.utc).timestamp()
        day_end = _dt.combine(target_date, _dtime(end_hour, 0), tzinfo=_tz.utc).timestamp()

        events = self.get_events_in_range(user_id, day_start, day_end)
        busy = [(e.start_ts_utc, e.end_ts_utc) for e in events if e.status != "cancelled"]
        busy.sort()

        slots = []
        current = day_start
        for (bs, be) in busy:
            if current < bs and (bs - current) >= duration_minutes * 60:
                slots.append({
                    "start": current,
                    "end": bs,
                    "start_iso": _dt.fromtimestamp(current, tz=_tz.utc).isoformat(),
                    "end_iso": _dt.fromtimestamp(bs, tz=_tz.utc).isoformat(),
                })
            current = max(current, be)
        if current < day_end and (day_end - current) >= duration_minutes * 60:
            slots.append({
                "start": current,
                "end": day_end,
                "start_iso": _dt.fromtimestamp(current, tz=_tz.utc).isoformat(),
                "end_iso": _dt.fromtimestamp(day_end, tz=_tz.utc).isoformat(),
            })
        return slots

    def get_pending_today(self, user_id: str) -> list[ScheduleEvent]:
        """Retorna eventos pending para el día actual."""
        from datetime import datetime as _dt, time as _dtime, timedelta as _td, timezone as _tz
        now = _dt.now(_tz.utc)
        day_start = _dt.combine(now.date(), _dtime.min, tzinfo=_tz.utc).timestamp()
        day_end = day_start + 86400
        events = self.get_events_in_range(user_id, day_start, day_end)
        return [e for e in events if e.status == "pending"]

    def create_recurring_series(
        self,
        user_id: str,
        title: str,
        category: str,
        start_ts_utc: float,
        end_ts_utc: float,
        recurrence: str,
        notes: str = "",
        weeks: int = 16,
        reminder_profile: Optional[str] = None,
    ) -> list[ScheduleEvent]:
        """Crea N instancias semanales/diarias de un evento. Retorna todos los creados."""
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        events: list[ScheduleEvent] = []
        duration_secs = end_ts_utc - start_ts_utc

        if recurrence.startswith("weekly"):
            step = _td(weeks=1)
        elif recurrence == "daily":
            step = _td(days=1)
        else:
            step = _td(weeks=1)

        current_start = _dt.fromtimestamp(start_ts_utc, tz=_tz.utc)
        parent_id: Optional[int] = None

        for _ in range(weeks):
            ts_start = current_start.timestamp()
            ts_end = ts_start + duration_secs
            ev = self.create_event(
                user_id=user_id,
                title=title,
                category=category,
                start_ts_utc=ts_start,
                end_ts_utc=ts_end,
                notes=notes,
                recurrence=recurrence,
                parent_id=parent_id,
                reminder_profile=reminder_profile,
            )
            if parent_id is None:
                parent_id = ev.id
                # Actualizar el primer evento para que su parent_id apunte a sí mismo
                with self._lock, sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "UPDATE schedule_events SET parent_id=? WHERE id=?",
                        (parent_id, parent_id),
                    )
                    conn.commit()
                ev.parent_id = parent_id
            events.append(ev)
            current_start += step

        return events

    def mark_notified(self, event_id: int) -> None:
        """Marca un evento como ya notificado para no repetir avisos."""
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE schedule_events SET notified_at=? WHERE id=?",
                (time.time(), event_id),
            )
            conn.commit()

    def get_upcoming_unnotified(
        self,
        user_id: str,
        window_secs: float = 1200,
    ) -> list[ScheduleEvent]:
        """Retorna eventos que empiezan en los próximos `window_secs` segundos y no han sido notificados."""
        now = time.time()
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, user_id, title, category, start_ts_utc, end_ts_utc, "
                "notes, created_at, "
                "COALESCE(status, 'pending'), completed_at, rescheduled_from, "
                "COALESCE(gcal_id, ''), notified_at, recurrence, parent_id, reminder_profile "
                "FROM schedule_events "
                "WHERE user_id=? AND status='pending' "
                "AND start_ts_utc > ? AND start_ts_utc <= ? "
                "AND notified_at IS NULL",
                (user_id, now, now + window_secs),
            ).fetchall()
        return [ScheduleEvent(*row) for row in rows]


_singleton: Optional[ScheduleService] = None
_singleton_lock = threading.Lock()


def get_schedule_service() -> ScheduleService:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ScheduleService()
    return _singleton
