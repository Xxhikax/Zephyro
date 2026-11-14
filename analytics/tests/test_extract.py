import sqlite3
from datetime import date, datetime, timezone

import pyarrow.parquet as pq
import pytest

from zephyro_analytics import extract, synthetic

END = date(2026, 3, 31)


@pytest.fixture
def sources(tmp_path):
    synthetic.ensure_until(tmp_path, END, days_back=5, seed=1)
    return tmp_path


def _all(sources):
    calls, _ = extract.extract_api_calls(sources / "api_usage.db", 0)
    requests, steps, _ = extract.extract_requests(sources / "task_envelopes.db", {}, "salt")
    turns, _ = extract.extract_turns(sources / "conversation_history.db", 0, "salt")
    return [calls, requests, steps, turns]


# ── synthetic sources ────────────────────────────────────────────────────────

def test_synthetic_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    synthetic.ensure_until(a, END, days_back=3, seed=9)
    synthetic.ensure_until(b, END, days_back=3, seed=9)
    rows = lambda d: sqlite3.connect(d / "api_usage.db").execute("SELECT * FROM api_calls").fetchall()
    assert rows(a) == rows(b) and rows(a)


def test_ensure_until_only_appends_missing_days(tmp_path):
    assert len(synthetic.ensure_until(tmp_path, END, days_back=4)) == 4
    assert synthetic.ensure_until(tmp_path, END, days_back=4) == []
    assert synthetic.ensure_until(tmp_path, date(2026, 4, 1), days_back=4) == [date(2026, 4, 1)]


# ── PII ──────────────────────────────────────────────────────────────────────

def test_no_free_text_reaches_the_landing_zone(sources):
    banned = {"user_input", "final_response", "assistant_response", "conversation_title",
              "importance_reasoning", "args", "result_summary", "error", "user_name", "session_id"}
    for batch in _all(sources):
        assert not banned & set(batch.schema.names), batch.table
        for row in batch.rows:
            for col, value in row.items():
                # Every synthetic free-text field is a multi-word sentence.
                assert not (isinstance(value, str) and " " in value), (batch.table, col, value)


def test_pseudonymize_is_stable_salted_and_null_safe():
    assert extract.pseudonymize("bruno", "s1") == extract.pseudonymize("bruno", "s1")
    assert extract.pseudonymize("bruno", "s1") != extract.pseudonymize("bruno", "s2")
    assert extract.pseudonymize(None, "s1") is None and extract.pseudonymize("", "s1") is None


@pytest.mark.parametrize("message,expected", [
    (None, None),
    ("APITimeoutError: Request timed out.", "APITimeoutError"),
    ("Error code: 429 - {'type': 'rate_limit_error'}", "http_429"),
    ("something odd with sk-ant-secret in it", "other"),
])
def test_error_type_never_keeps_the_message(message, expected):
    assert extract.error_type(message) == expected


# ── incremental extraction ───────────────────────────────────────────────────

def test_second_extraction_without_new_rows_is_empty(sources):
    calls, mark = extract.extract_api_calls(sources / "api_usage.db", 0)
    assert calls.rows
    again, mark2 = extract.extract_api_calls(sources / "api_usage.db", mark)
    assert again.rows == [] and mark2 == mark

    req, _, rmark = extract.extract_requests(sources / "task_envelopes.db", {}, "salt")
    assert req.rows
    req2, steps2, rmark2 = extract.extract_requests(sources / "task_envelopes.db", rmark, "salt")
    assert req2.rows == [] and steps2.rows == [] and rmark2 == rmark


def test_new_day_extracts_only_the_new_rows(sources):
    _, mark = extract.extract_api_calls(sources / "api_usage.db", 0)
    synthetic.ensure_until(sources, date(2026, 4, 1), days_back=5, seed=1)
    new, _ = extract.extract_api_calls(sources / "api_usage.db", mark)
    assert new.rows and all(r["call_id"] > mark for r in new.rows)


def test_request_written_later_with_the_same_timestamp_is_not_lost(tmp_path):
    db = tmp_path / "task_envelopes.db"
    synthetic.ensure_until(tmp_path, END, days_back=1)
    con = sqlite3.connect(db)
    ts = con.execute("SELECT max(started_at) FROM task_envelopes").fetchone()[0]
    _, _, mark = extract.extract_requests(db, {}, "salt")

    con.execute("INSERT INTO task_envelopes (request_id, steps_json, started_at, finished_at) VALUES (?, '[]', ?, ?)",
                ("late-same-ts", ts, ts))
    con.commit()
    con.close()
    late, _, mark2 = extract.extract_requests(db, mark, "salt")
    assert [r["request_id"] for r in late.rows] == ["late-same-ts"]
    assert "late-same-ts" in mark2["ids_at_epoch"]


def test_sources_are_opened_read_only(sources):
    with pytest.raises(sqlite3.OperationalError):
        with extract._connect(sources / "api_usage.db") as con:
            con.execute("DELETE FROM api_calls")


# ── landing zone ─────────────────────────────────────────────────────────────

def test_write_batch_partitions_by_load_date_and_skips_empty(tmp_path, sources):
    calls, _ = extract.extract_api_calls(sources / "api_usage.db", 0)
    loaded = datetime(2026, 4, 2, 6, 0, tzinfo=timezone.utc)
    path = extract.write_batch(calls, tmp_path / "raw", "run1", loaded)
    assert path.parent.name == "load_date=2026-04-02"
    table = pq.read_table(path)
    assert table.num_rows == len(calls.rows) and "_loaded_at" in table.column_names

    empty = extract.Batch("api_calls", [], extract.API_CALLS_SCHEMA)
    assert extract.write_batch(empty, tmp_path / "raw", "run2", loaded) is None


def test_watermarks_round_trip(tmp_path):
    path = tmp_path / "state" / "watermarks.json"
    assert extract.load_watermarks(path) == {}
    extract.save_watermarks(path, {"api_calls": 3, "requests": {"epoch": 1.5, "ids_at_epoch": ["a"]}})
    assert extract.load_watermarks(path)["requests"]["ids_at_epoch"] == ["a"]
