"""Incremental extraction from Zephyro's SQLite stores into a Parquet landing zone.

Each run reads only the rows past the stored watermark and writes them to
``raw/<table>/load_date=YYYY-MM-DD/part-<run_id>.parquet``. The landing zone is
append-only; deduplication happens in the dbt staging models.

PII policy (applied here, before anything touches disk):
- free-text columns (user input, answers, titles, tool args and summaries, raw
  error messages) are never extracted; only their length is kept;
- user and session identifiers are replaced by a salted SHA-256;
- error messages become a coarse ``error_type`` because they can echo request
  payloads or credentials.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

_ERROR_NAME = re.compile(r"\b([A-Z][A-Za-z]*(?:Error|Exception|Timeout))\b")
_HTTP_STATUS = re.compile(r"\b([45]\d\d)\b")


def pseudonymize(value: str | None, salt: str) -> str | None:
    if value is None or value == "":
        return None
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:16]


def error_type(message: str | None) -> str | None:
    if not message:
        return None
    if m := _ERROR_NAME.search(message):
        return m.group(1)
    if m := _HTTP_STATUS.search(message):
        return f"http_{m.group(1)}"
    return "other"


def _text_len(value: str | None) -> int | None:
    return None if value is None else len(value)


def _epoch(value) -> float | None:
    """SQLite stores some timestamps as epoch floats and others as ISO strings."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@dataclass
class Batch:
    table: str
    rows: list[dict]
    schema: pa.Schema

    def to_arrow(self) -> pa.Table:
        return pa.Table.from_pylist(self.rows, schema=self.schema)


API_CALLS_SCHEMA = pa.schema([
    ("call_id", pa.int64()), ("called_at_epoch", pa.float64()), ("provider", pa.string()),
    ("model", pa.string()), ("tokens_in", pa.int64()), ("tokens_out", pa.int64()),
    ("latency_ms", pa.float64()), ("cost_usd", pa.float64()), ("success", pa.bool_()),
    ("error_type", pa.string()),
])

REQUESTS_SCHEMA = pa.schema([
    ("request_id", pa.string()), ("started_at_epoch", pa.float64()), ("finished_at_epoch", pa.float64()),
    ("intent", pa.string()), ("channel", pa.string()), ("success", pa.bool_()),
    ("duration_ms", pa.float64()), ("complexity_score", pa.float64()),
    ("capability_mismatch", pa.bool_()), ("user_feedback", pa.string()),
    ("total_cost_usd", pa.float64()), ("total_tokens", pa.int64()), ("user_key", pa.string()),
    ("input_chars", pa.int64()), ("response_chars", pa.int64()), ("step_count", pa.int64()),
])

REQUEST_STEPS_SCHEMA = pa.schema([
    ("request_id", pa.string()), ("step_index", pa.int64()), ("skill_name", pa.string()),
    ("success", pa.bool_()), ("duration_ms", pa.float64()), ("provider", pa.string()),
    ("model", pa.string()), ("tokens_in", pa.int64()), ("tokens_out", pa.int64()),
    ("cost_usd", pa.float64()), ("error_type", pa.string()),
])

TURNS_SCHEMA = pa.schema([
    ("turn_id", pa.int64()), ("session_key", pa.string()), ("user_key", pa.string()),
    ("user_role", pa.string()), ("turn_at_epoch", pa.float64()), ("importance_level", pa.string()),
    ("importance_score", pa.float64()), ("saved_to_memory", pa.bool_()), ("manually_marked", pa.bool_()),
    ("intent", pa.string()), ("complexity", pa.string()), ("elapsed_s", pa.float64()),
    ("mode_used", pa.string()), ("input_chars", pa.int64()), ("response_chars", pa.int64()),
])


def _connect(db: Path) -> sqlite3.Connection:
    # Read-only: the pipeline must never be able to write into Zephyro's stores.
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _bool(value) -> bool | None:
    return None if value is None else bool(value)


def extract_api_calls(db: Path, since_id: int) -> tuple[Batch, int]:
    with _connect(db) as con:
        rows = con.execute(
            "SELECT id, ts, provider, model, tokens_in, tokens_out, latency_ms, cost_usd, success, error "
            "FROM api_calls WHERE id > ? ORDER BY id", (since_id,)).fetchall()
    out = [{
        "call_id": r["id"], "called_at_epoch": _epoch(r["ts"]), "provider": r["provider"],
        "model": r["model"], "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"],
        "latency_ms": r["latency_ms"], "cost_usd": r["cost_usd"], "success": _bool(r["success"]),
        "error_type": error_type(r["error"]),
    } for r in rows]
    watermark = max((r["call_id"] for r in out), default=since_id)
    return Batch("api_calls", out, API_CALLS_SCHEMA), watermark


def extract_requests(db: Path, mark: dict, salt: str) -> tuple[Batch, Batch, dict]:
    """`started_at` is not unique, so a strict `>` cut could skip a row written later
    with the same timestamp. The cut is inclusive (>=) and the watermark also keeps
    the ids already seen at that exact instant, so nothing is lost or re-read."""
    since = mark.get("epoch", 0.0)
    seen = set(mark.get("ids_at_epoch", []))
    with _connect(db) as con:
        rows = con.execute(
            "SELECT * FROM task_envelopes WHERE started_at >= ? ORDER BY started_at", (since,)).fetchall()
    rows = [r for r in rows if not (_epoch(r["started_at"]) == since and r["request_id"] in seen)]
    requests, steps = [], []
    for r in rows:
        parsed = json.loads(r["steps_json"] or "[]")
        requests.append({
            "request_id": r["request_id"], "started_at_epoch": _epoch(r["started_at"]),
            "finished_at_epoch": _epoch(r["finished_at"]), "intent": r["intent"], "channel": r["channel_type"],
            "success": _bool(r["success"]), "duration_ms": r["duration_ms"],
            "complexity_score": r["complexity_score"], "capability_mismatch": _bool(r["capability_mismatch"]),
            "user_feedback": r["user_feedback"], "total_cost_usd": r["total_cost_usd"],
            "total_tokens": r["total_tokens"], "user_key": pseudonymize(r["user_name"], salt),
            "input_chars": _text_len(r["user_input"]), "response_chars": _text_len(r["final_response"]),
            "step_count": len(parsed),
        })
        for i, s in enumerate(parsed):
            steps.append({
                "request_id": r["request_id"], "step_index": i, "skill_name": s.get("skill_name"),
                "success": _bool(s.get("success")), "duration_ms": s.get("duration_ms"),
                "provider": s.get("provider"), "model": s.get("model"), "tokens_in": s.get("tokens_in"),
                "tokens_out": s.get("tokens_out"), "cost_usd": s.get("cost_usd"),
                "error_type": error_type(s.get("error")),
            })
    if requests:
        top = max(r["started_at_epoch"] for r in requests)
        at_top = {r["request_id"] for r in requests if r["started_at_epoch"] == top}
        new_mark = {"epoch": top, "ids_at_epoch": sorted(at_top | (seen if top == since else set()))}
    else:
        new_mark = {"epoch": since, "ids_at_epoch": sorted(seen)}
    return (Batch("requests", requests, REQUESTS_SCHEMA),
            Batch("request_steps", steps, REQUEST_STEPS_SCHEMA), new_mark)


def extract_turns(db: Path, since_id: int, salt: str) -> tuple[Batch, int]:
    with _connect(db) as con:
        rows = con.execute(
            "SELECT id, session_id, user_name, user_role, timestamp, importance_level, importance_score, "
            "saved_to_memory, manually_marked, intent, complexity, elapsed_time, mode_used, "
            "length(user_input) AS input_chars, length(assistant_response) AS response_chars "
            "FROM conversations WHERE id > ? ORDER BY id", (since_id,)).fetchall()
    out = [{
        "turn_id": r["id"], "session_key": pseudonymize(r["session_id"], salt),
        "user_key": pseudonymize(r["user_name"], salt), "user_role": r["user_role"],
        "turn_at_epoch": _epoch(r["timestamp"]), "importance_level": r["importance_level"],
        "importance_score": r["importance_score"], "saved_to_memory": _bool(r["saved_to_memory"]),
        "manually_marked": _bool(r["manually_marked"]), "intent": r["intent"],
        "complexity": r["complexity"], "elapsed_s": r["elapsed_time"], "mode_used": r["mode_used"],
        "input_chars": r["input_chars"], "response_chars": r["response_chars"],
    } for r in rows]
    watermark = max((r["turn_id"] for r in out), default=since_id)
    return Batch("turns", out, TURNS_SCHEMA), watermark


# ── landing zone + watermarks ────────────────────────────────────────────────

def load_watermarks(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_watermarks(path: Path, marks: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(marks, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)  # atomic: a crash never leaves a half-written watermark file


def write_batch(batch: Batch, raw_root: Path, run_id: str, loaded_at: datetime) -> Path | None:
    """Returns the written file, or None when the batch is empty (no empty files)."""
    if not batch.rows:
        return None
    table = batch.to_arrow().append_column(
        "_loaded_at", pa.array([loaded_at] * len(batch.rows), type=pa.timestamp("us", tz="UTC")))
    target = raw_root / batch.table / f"load_date={loaded_at:%Y-%m-%d}" / f"part-{run_id}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, target)
    return target
