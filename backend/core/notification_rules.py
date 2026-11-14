"""Reglas declarativas de notificación + eventos de dominio.

Fase 1 del roadmap de skills (ADR-011). Hasta ahora cada checker de
``core/proactive_notifier.py`` decidía POR SÍ MISMO si notificar, con umbrales
hardcodeados y dedup en un dict en memoria que se perdía en cada reinicio.

Acá vive la parte declarativa:

- **Eventos de dominio** (``EVENT_*``): lo que pasó, publicado por quien lo sabe
  (una skill, un service, el notifier). El productor NO decide si se notifica.
- **NotificationRule**: para un tipo de evento, qué severidad mínima importa, qué
  campos del payload tienen que matchear, por qué canales sale, cada cuánto y en
  qué horas NO molestar.
- **RuleStore**: persistencia SQLite (``storage/notifications.db``) de reglas +
  log de entregas, que es lo que hace que el dedup **sobreviva un reinicio**.

La evaluación vive en ``core/notification_center.py``.

Decisión de diseño: la condición de una regla NO es una expresión evaluable. Es
un conjunto de campos tipados (``min_severity`` + ``match`` + comparador
numérico opcional). Un `eval()` de reglas sería más flexible y una puerta
abierta a ejecución arbitraria desde la DB, además de indebuggeable.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("zephyro.notifications")

# ── Eventos de dominio ────────────────────────────────────────────────────────
# Convención: "<dominio>.<hecho_en_pasado_o_estado>". Son los eventos que el
# NotificationCenter sabe rutear; el event_bus (core/event_bus.py) sigue siendo
# el transporte y mantiene sus propios eventos técnicos (GIT_COMMIT, LOG_ERROR…).

EVENT_SCHEDULE_UPCOMING = "schedule.event_upcoming"
EVENT_SCHEDULE_MISSED = "schedule.event_missed"
EVENT_REMINDER_DUE = "reminder.due"
EVENT_BRIEFING_READY = "briefing.ready"
EVENT_CANVAS_ASSIGNMENT_DUE = "canvas.assignment_due"
EVENT_CANVAS_SUBMISSION_MISSING = "canvas.submission_missing"
EVENT_EXPENSES_BUDGET_THRESHOLD = "expenses.budget_threshold"
EVENT_CI_RUN_FAILED = "ci.run_failed"

KNOWN_EVENTS = (
    EVENT_SCHEDULE_UPCOMING,
    EVENT_SCHEDULE_MISSED,
    EVENT_REMINDER_DUE,
    EVENT_BRIEFING_READY,
    EVENT_CANVAS_ASSIGNMENT_DUE,
    EVENT_CANVAS_SUBMISSION_MISSING,
    EVENT_EXPENSES_BUDGET_THRESHOLD,
    EVENT_CI_RUN_FAILED,
)

# ── Severidad ─────────────────────────────────────────────────────────────────
# `urgent` es el único nivel que ignora las horas de silencio.
SEVERITY_ORDER = {"info": 0, "warn": 1, "urgent": 2}
DEFAULT_SEVERITY = "info"

CHANNEL_WS = "ws"
CHANNEL_TELEGRAM = "telegram"
# CHANNEL_ALL sale por TODOS los canales registrados (channel_manager.broadcast_text),
# no solo Telegram. Existe porque el briefing matutino ya salía así y migrarlo a
# `telegram` le habría sacado el briefing a WhatsApp sin que nadie lo pidiera.
CHANNEL_ALL = "channels"
DEFAULT_CHANNELS = (CHANNEL_WS, CHANNEL_TELEGRAM)


def normalize_severity(value: Optional[str]) -> str:
    v = (value or DEFAULT_SEVERITY).strip().lower()
    return v if v in SEVERITY_ORDER else DEFAULT_SEVERITY


@dataclass
class NotificationEvent:
    """Un hecho del dominio, listo para que las reglas decidan qué hacer con él.

    ``dedup_key`` es la identidad del hecho para el log de entregas: dos eventos
    con la misma clave son el mismo aviso y el cooldown aplica entre ellos. Si no
    se pasa, se deriva de event_type + title.
    """
    event_type: str
    title: str
    message: str = ""
    severity: str = DEFAULT_SEVERITY
    dedup_key: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.severity = normalize_severity(self.severity)
        if not self.dedup_key:
            self.dedup_key = f"{self.event_type}:{self.title}"

    # Claves del payload que son instrucciones de entrega, no datos para la UI.
    _CONTROL_KEYS = ("telegram_text", "broadcast_all", "keyboard")

    def to_ws_payload(self) -> dict:
        """Payload WebSocket. Mantiene la forma que ya consume el frontend
        (`type: reminder` + subtype) para no romper `src/chat.js`.

        El payload del evento pisa los defaults a propósito: así un productor
        migrado conserva su `subtype`/`type` histórico en vez de heredar el
        nombre del evento de dominio.
        """
        out = {
            "type": "reminder",
            "subtype": self.event_type,
            "title": self.title,
            "message": self.message,
            "severity": self.severity,
        }
        out.update({
            k: v for k, v in self.payload.items() if k not in self._CONTROL_KEYS
        })
        return out


@dataclass
class NotificationRule:
    """Regla declarativa: qué hacer cuando llega un evento de dominio."""
    event_type: str
    name: str
    id: Optional[int] = None
    enabled: bool = True
    min_severity: str = DEFAULT_SEVERITY
    # match: {campo_del_payload: valor | [valores]} — TODOS deben matchear.
    match: dict[str, Any] = field(default_factory=dict)
    channels: list[str] = field(default_factory=lambda: list(DEFAULT_CHANNELS))
    # Segundos de silencio para una misma dedup_key. 0 = sin cooldown.
    cooldown_seconds: int = 0
    # Horas locales [quiet_from, quiet_to) donde solo pasa severity=urgent.
    # Soporta ventanas que cruzan medianoche (22 → 7).
    quiet_from: Optional[int] = None
    quiet_to: Optional[int] = None

    def __post_init__(self) -> None:
        self.min_severity = normalize_severity(self.min_severity)
        if not self.channels:
            self.channels = list(DEFAULT_CHANNELS)

    def severity_allows(self, severity: str) -> bool:
        return SEVERITY_ORDER[normalize_severity(severity)] >= SEVERITY_ORDER[self.min_severity]

    def payload_matches(self, payload: dict) -> bool:
        for key, expected in self.match.items():
            actual = payload.get(key)
            if isinstance(expected, (list, tuple, set)):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    def in_quiet_hours(self, hour: int) -> bool:
        if self.quiet_from is None or self.quiet_to is None:
            return False
        if self.quiet_from == self.quiet_to:
            return False
        if self.quiet_from < self.quiet_to:
            return self.quiet_from <= hour < self.quiet_to
        # Ventana que cruza medianoche: 22 → 7
        return hour >= self.quiet_from or hour < self.quiet_to

    def to_row(self) -> tuple:
        return (
            self.event_type,
            self.name,
            1 if self.enabled else 0,
            self.min_severity,
            json.dumps(self.match, ensure_ascii=False),
            json.dumps(self.channels),
            int(self.cooldown_seconds),
            self.quiet_from,
            self.quiet_to,
            time.time(),
        )

    @classmethod
    def from_row(cls, row: tuple) -> "NotificationRule":
        (rid, event_type, name, enabled, min_sev, match_json,
         channels_json, cooldown, quiet_from, quiet_to, _updated) = row
        return cls(
            id=int(rid),
            event_type=event_type,
            name=name,
            enabled=bool(enabled),
            min_severity=min_sev,
            match=json.loads(match_json) if match_json else {},
            channels=json.loads(channels_json) if channels_json else list(DEFAULT_CHANNELS),
            cooldown_seconds=int(cooldown or 0),
            quiet_from=quiet_from,
            quiet_to=quiet_to,
        )


# ── Reglas por defecto ────────────────────────────────────────────────────────
# Espejan el comportamiento que hoy está hardcodeado en proactive_notifier, para
# que la Fase 1 no cambie lo que Bruno ya recibe. Se siembran una sola vez; si
# después las edita, el seed no las pisa.
DEFAULT_RULES: tuple[NotificationRule, ...] = (
    NotificationRule(
        event_type=EVENT_SCHEDULE_UPCOMING,
        name="Eventos de agenda próximos",
        min_severity="info",
    ),
    NotificationRule(
        event_type=EVENT_SCHEDULE_MISSED,
        name="Eventos de agenda perdidos",
        min_severity="warn",
    ),
    NotificationRule(
        event_type=EVENT_REMINDER_DUE,
        name="Recordatorios",
        min_severity="info",
    ),
    NotificationRule(
        event_type=EVENT_BRIEFING_READY,
        name="Briefing matutino",
        min_severity="info",
        # CHANNEL_ALL: el briefing siempre salió por todos los canales.
        channels=[CHANNEL_WS, CHANNEL_ALL],
        cooldown_seconds=12 * 3600,
    ),
    NotificationRule(
        event_type=EVENT_CANVAS_SUBMISSION_MISSING,
        name="Entregas faltantes de Canvas",
        min_severity="warn",
    ),
    NotificationRule(
        event_type=EVENT_CANVAS_ASSIGNMENT_DUE,
        name="Entregas próximas de Canvas",
        min_severity="info",
        cooldown_seconds=4 * 3600,
    ),
    NotificationRule(
        event_type=EVENT_EXPENSES_BUDGET_THRESHOLD,
        name="Presupuesto al límite o excedido",
        min_severity="warn",
        # El nivel 'ok' de budget_status no se notifica nunca.
        match={"level": ["warn", "over"]},
        cooldown_seconds=12 * 3600,
        quiet_from=23,
        quiet_to=8,
    ),
    NotificationRule(
        event_type=EVENT_CI_RUN_FAILED,
        name="CI fallido",
        min_severity="warn",
        cooldown_seconds=1800,
        quiet_from=23,
        quiet_to=8,
    ),
)


class RuleStore:
    """Persistencia de reglas + log de entregas (dedup que sobrevive reinicios)."""

    DB_PATH = str(
        Path(__file__).resolve().parent.parent / "storage" / "notifications.db"
    )

    def __init__(self, db_path: Optional[str] = None, seed_defaults: bool = True):
        self._db_path = db_path or self.DB_PATH
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()
        if seed_defaults:
            self.seed_defaults()

    def _init_db(self) -> None:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    min_severity TEXT NOT NULL DEFAULT 'info',
                    match_json TEXT NOT NULL DEFAULT '{}',
                    channels_json TEXT NOT NULL DEFAULT '[]',
                    cooldown_seconds INTEGER NOT NULL DEFAULT 0,
                    quiet_from INTEGER,
                    quiet_to INTEGER,
                    updated_at REAL NOT NULL,
                    UNIQUE(event_type, name)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_log (
                    dedup_key TEXT NOT NULL,
                    rule_id INTEGER NOT NULL,
                    sent_at REAL NOT NULL,
                    PRIMARY KEY (dedup_key, rule_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notification_rules_event "
                "ON notification_rules(event_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notification_log_sent "
                "ON notification_log(sent_at)"
            )
            conn.commit()

    # ── Reglas ────────────────────────────────────────────────────────────

    def seed_defaults(self) -> int:
        """Inserta las reglas por defecto que falten. NO pisa las existentes:
        si Bruno editó una regla, el seed de un reinicio no debe revertirla."""
        inserted = 0
        with self._lock, sqlite3.connect(self._db_path) as conn:
            for rule in DEFAULT_RULES:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_rules (
                        event_type, name, enabled, min_severity, match_json,
                        channels_json, cooldown_seconds, quiet_from, quiet_to, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rule.to_row(),
                )
                inserted += cur.rowcount if cur.rowcount > 0 else 0
            conn.commit()
        if inserted:
            log.info("[notifications] %d reglas por defecto sembradas", inserted)
        return inserted

    _SELECT = (
        "SELECT id, event_type, name, enabled, min_severity, match_json, "
        "channels_json, cooldown_seconds, quiet_from, quiet_to, updated_at "
        "FROM notification_rules"
    )

    def rules_for(self, event_type: str, only_enabled: bool = True) -> list[NotificationRule]:
        sql = f"{self._SELECT} WHERE event_type = ?"
        params: list = [event_type]
        if only_enabled:
            sql += " AND enabled = 1"
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [NotificationRule.from_row(r) for r in rows]

    def all_rules(self) -> list[NotificationRule]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(f"{self._SELECT} ORDER BY event_type, name").fetchall()
        return [NotificationRule.from_row(r) for r in rows]

    def event_types(self, only_enabled: bool = True) -> list[str]:
        sql = "SELECT DISTINCT event_type FROM notification_rules"
        if only_enabled:
            sql += " WHERE enabled = 1"
        with sqlite3.connect(self._db_path) as conn:
            return [r[0] for r in conn.execute(sql).fetchall()]

    def upsert_rule(self, rule: NotificationRule) -> NotificationRule:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO notification_rules (
                    event_type, name, enabled, min_severity, match_json,
                    channels_json, cooldown_seconds, quiet_from, quiet_to, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_type, name) DO UPDATE SET
                    enabled = excluded.enabled,
                    min_severity = excluded.min_severity,
                    match_json = excluded.match_json,
                    channels_json = excluded.channels_json,
                    cooldown_seconds = excluded.cooldown_seconds,
                    quiet_from = excluded.quiet_from,
                    quiet_to = excluded.quiet_to,
                    updated_at = excluded.updated_at
                """,
                rule.to_row(),
            )
            conn.commit()
            if cur.lastrowid:
                rule.id = int(cur.lastrowid)
        return rule

    def set_enabled(self, rule_id: int, enabled: bool) -> bool:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE notification_rules SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, time.time(), rule_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_rule(self, rule_id: int) -> bool:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM notification_rules WHERE id = ?", (rule_id,))
            conn.commit()
            return cur.rowcount > 0

    # ── Log de entregas (dedup persistente) ───────────────────────────────

    def last_sent_at(self, dedup_key: str, rule_id: int) -> Optional[float]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT sent_at FROM notification_log WHERE dedup_key = ? AND rule_id = ?",
                (dedup_key, rule_id),
            ).fetchone()
        return float(row[0]) if row else None

    def record_sent(self, dedup_key: str, rule_id: int, sent_at: Optional[float] = None) -> None:
        ts = sent_at if sent_at is not None else time.time()
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO notification_log (dedup_key, rule_id, sent_at)
                VALUES (?, ?, ?)
                ON CONFLICT(dedup_key, rule_id) DO UPDATE SET sent_at = excluded.sent_at
                """,
                (dedup_key, rule_id, ts),
            )
            conn.commit()

    def purge_log(self, older_than_seconds: float = 30 * 86400) -> int:
        cutoff = time.time() - older_than_seconds
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM notification_log WHERE sent_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount


_store: Optional[RuleStore] = None
_store_lock = threading.Lock()


def get_rule_store() -> RuleStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = RuleStore()
    return _store
