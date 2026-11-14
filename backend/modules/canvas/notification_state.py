"""Persistent notification state for Canvas alerts (SQLite-backed).

States for missing assignments:
  new         → not yet notified
  asked       → sent [SÍ/NO] interactive message (still waiting for response)
  will_submit → user said yes; remind at 8am/8pm (same interactive buttons)
  done        → terminal; never notify again (user said no, or assignment not submittable)

States for pending_global (all upcoming assignments pooled):
  new         → default; notify at 8am/8pm window
  ok          → user clicked OK; next_notify_at = now + 4h rolling
  ignored     → user clicked IGNORAR; next_notify_at = tomorrow midnight → then back to new
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent.parent / "storage" / "canvas_notifications.db"

_DDL = """
CREATE TABLE IF NOT EXISTS canvas_notification_state (
    item_id        TEXT NOT NULL,
    item_type      TEXT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'new',
    next_notify_at REAL NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    PRIMARY KEY (item_id, item_type)
)
"""


class CanvasNotificationState:
    def __init__(self, db_path: Path = DB_PATH):
        self._db = db_path
        self._db.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_DDL)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db))

    def get(self, item_id: str, item_type: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state, next_notify_at FROM canvas_notification_state "
                "WHERE item_id=? AND item_type=?",
                (item_id, item_type),
            ).fetchone()
        if row is None:
            return None
        return {"state": row[0], "next_notify_at": row[1]}

    def set_state(
        self,
        item_id: str,
        item_type: str,
        state: str,
        next_notify_at: float = 0.0,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO canvas_notification_state "
                "(item_id, item_type, state, next_notify_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id, item_type) DO UPDATE SET "
                "state=excluded.state, next_notify_at=excluded.next_notify_at, "
                "updated_at=excluded.updated_at",
                (item_id, item_type, state, next_notify_at, now, now),
            )

    @staticmethod
    def tomorrow_midnight() -> float:
        tomorrow = datetime.now().date() + timedelta(days=1)
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day).timestamp()


_singleton: Optional[CanvasNotificationState] = None


def get_canvas_notification_state() -> CanvasNotificationState:
    global _singleton
    if _singleton is None:
        _singleton = CanvasNotificationState()
    return _singleton
