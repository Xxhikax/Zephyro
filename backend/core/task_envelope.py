"""
TaskEnvelope — captura estructurada de cada tarea completa para el sistema
de aprendizaje (Parte B / Hermes-style).

Cada interacción produce un TaskEnvelope que registra:
    - Pregunta del usuario, intent del router
    - Skills/herramientas invocadas con sus argumentos y resultados
    - Resultado final + métricas (duración, complexity, success)
    - Feedback explícito o implícito del usuario
    - Provider/modelo usado y costo estimado por step

Persistencia: SQLite en `storage/task_envelopes.db`. La DB es auto-archival:
envelopes detallados se mantienen 30 días, después solo metadata agregada.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Optional

log = logging.getLogger("zephyro.task_envelope")


# ── Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class StepRecord:
    skill_name: str
    args: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    success: bool = True
    duration_ms: int = 0
    provider: Optional[str] = None  # ej. "anthropic"
    model: Optional[str] = None     # ej. ANTHROPIC_MODELS["balanced"] (core/model_catalog.py)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None


def _coerce_float(value: Any) -> float:
    """Coerce started_at/finished_at a float, manejando strings legacy de SQLite."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            try:
                from datetime import datetime
                return datetime.fromisoformat(value).timestamp()
            except Exception:
                return 0.0
    if hasattr(value, "timestamp"):
        return value.timestamp()
    return 0.0


@dataclass
class TaskEnvelope:
    request_id: str
    user_input: str
    intent: str = ""
    steps: list[StepRecord] = field(default_factory=list)
    final_response: str = ""
    success: bool = True
    duration_ms: int = 0
    complexity_score: float = 0.0
    capability_mismatch: bool = False
    user_feedback: Optional[int] = None  # -1 / 0 / 1 / None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    channel_type: str = "websocket"
    user_name: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_step(self, step: StepRecord) -> None:
        self.steps.append(step)

    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    def total_tokens(self) -> int:
        return sum(s.tokens_in + s.tokens_out for s in self.steps)

    def to_row(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user_input": self.user_input,
            "intent": self.intent,
            "steps_json": json.dumps(
                [asdict(s) for s in self.steps], ensure_ascii=False
            ),
            "final_response": self.final_response,
            "success": int(self.success),
            "duration_ms": self.duration_ms,
            "complexity_score": self.complexity_score,
            "capability_mismatch": int(self.capability_mismatch),
            "user_feedback": self.user_feedback,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "channel_type": self.channel_type,
            "user_name": self.user_name,
            "metadata_json": json.dumps(self.metadata, ensure_ascii=False),
            "total_cost_usd": self.total_cost_usd(),
            "total_tokens": self.total_tokens(),
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TaskEnvelope":
        steps = [
            StepRecord(**s) for s in json.loads(row["steps_json"] or "[]")
        ]
        return cls(
            request_id=row["request_id"],
            user_input=row["user_input"],
            intent=row["intent"] or "",
            steps=steps,
            final_response=row["final_response"] or "",
            success=bool(row["success"]),
            duration_ms=row["duration_ms"] or 0,
            complexity_score=row["complexity_score"] or 0.0,
            capability_mismatch=bool(row["capability_mismatch"]),
            user_feedback=row["user_feedback"],
            started_at=_coerce_float(row["started_at"]),
            finished_at=_coerce_float(row["finished_at"]) if row["finished_at"] is not None else None,
            channel_type=row["channel_type"] or "websocket",
            user_name=row["user_name"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )


# ── Store ──────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_envelopes (
    request_id TEXT PRIMARY KEY,
    user_input TEXT NOT NULL,
    intent TEXT,
    steps_json TEXT,
    final_response TEXT,
    success INTEGER,
    duration_ms INTEGER,
    complexity_score REAL,
    capability_mismatch INTEGER,
    user_feedback INTEGER,
    started_at REAL NOT NULL,
    finished_at REAL,
    channel_type TEXT,
    user_name TEXT,
    metadata_json TEXT,
    total_cost_usd REAL,
    total_tokens INTEGER
);

CREATE INDEX IF NOT EXISTS idx_task_envelopes_started_at ON task_envelopes(started_at);
CREATE INDEX IF NOT EXISTS idx_task_envelopes_success ON task_envelopes(success);
CREATE INDEX IF NOT EXISTS idx_task_envelopes_user ON task_envelopes(user_name);
"""


class TaskEnvelopeStore:
    """SQLite-backed store. Thread-safe via lock."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or Path("storage/task_envelopes.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    def save(self, envelope: TaskEnvelope) -> None:
        if envelope.finished_at is None:
            envelope.finished_at = time.time()
        row = envelope.to_row()
        cols = list(row.keys())
        placeholders = ",".join(f":{c}" for c in cols)
        col_list = ",".join(cols)
        with self._conn() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO task_envelopes ({col_list}) VALUES ({placeholders})",
                row,
            )
            conn.commit()

    def get(self, request_id: str) -> Optional[TaskEnvelope]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM task_envelopes WHERE request_id = ?", (request_id,)
            )
            row = cur.fetchone()
            return TaskEnvelope.from_row(row) if row else None

    def recent(
        self, limit: int = 50, since: Optional[float] = None
    ) -> list[TaskEnvelope]:
        with self._conn() as conn:
            if since is not None:
                cur = conn.execute(
                    "SELECT * FROM task_envelopes WHERE started_at >= ? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (since, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM task_envelopes ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                )
            return [TaskEnvelope.from_row(r) for r in cur.fetchall()]

    def successful_complex(
        self, *, min_complexity: float = 0.6, min_steps: int = 3, limit: int = 50,
    ) -> list[TaskEnvelope]:
        """
        Tareas exitosas y complejas — input para skill_proposer.
        Nota: filtramos min_steps en Python porque steps_json es texto.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM task_envelopes "
                "WHERE success = 1 AND complexity_score >= ? "
                "ORDER BY started_at DESC LIMIT ?",
                (min_complexity, limit * 3),  # over-fetch para filtrar después
            )
            envelopes = [TaskEnvelope.from_row(r) for r in cur.fetchall()]
        return [e for e in envelopes if len(e.steps) >= min_steps][:limit]

    def usage_summary(self, since: float) -> dict[str, dict[str, float]]:
        """
        Agrega tokens/costo por (provider, model) desde `since`.
        Útil para `/providers/usage`.
        """
        envelopes = self.recent(limit=10_000, since=since)
        summary: dict[str, dict[str, float]] = {}
        for env in envelopes:
            for s in env.steps:
                if not s.provider:
                    continue
                key = f"{s.provider}/{s.model or 'unknown'}"
                bucket = summary.setdefault(
                    key, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0}
                )
                bucket["tokens_in"] += s.tokens_in
                bucket["tokens_out"] += s.tokens_out
                bucket["cost_usd"] += s.cost_usd
                bucket["calls"] += 1
        return summary

    def cleanup_old(self, max_age_days: float = 30.0) -> int:
        """Borra envelopes detallados más viejos que `max_age_days`."""
        cutoff = time.time() - (max_age_days * 86400.0)
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM task_envelopes WHERE started_at < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount


# ── Singleton ──────────────────────────────────────────────────────────

_store: Optional[TaskEnvelopeStore] = None


def get_envelope_store() -> TaskEnvelopeStore:
    global _store
    if _store is None:
        _store = TaskEnvelopeStore()
    return _store


def reset_envelope_store() -> None:
    """Solo para tests."""
    global _store
    _store = None


def make_request_id() -> str:
    return uuid.uuid4().hex[:12]
