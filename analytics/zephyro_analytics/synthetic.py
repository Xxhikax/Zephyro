"""Synthetic Zephyro telemetry with the same SQLite schema as the real stores.

The public repo and CI run the whole pipeline on this data, through the same
extractor that reads the real ``storage/*.db``. Generation is deterministic per day
(``random.Random(f"{seed}-{day}")``), and ``ensure_until`` only appends the days
that are missing, so re-running it is idempotent and later runs exercise the
incremental extraction.
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

# USD per 1M tokens (input, output). Kept in sync with dbt/seeds/model_prices.csv.
PRICES = {
    ("deepseek", "deepseek-flash"): (0.30, 1.20),
    ("deepseek", "deepseek-v4-pro"): (1.32, 3.96),
    ("anthropic", "claude-haiku-5-5"): (0.10, 0.50),
    ("anthropic", "claude-sonnet-5-5"): (2.00, 10.00),
    ("gemini", "gemini-2.5-flash"): (0.075, 0.30),
    ("ollama_local", "llama3.2:3b"): (0.0, 0.0),
}

# (provider, model): weight, median latency ms, lognormal sigma, error rate
MODELS = {
    ("deepseek", "deepseek-flash"): (0.45, 1900, 0.45, 0.03),
    ("deepseek", "deepseek-v4-pro"): (0.08, 5200, 0.55, 0.04),
    ("anthropic", "claude-haiku-5-5"): (0.15, 900, 0.35, 0.01),
    ("anthropic", "claude-sonnet-5-5"): (0.12, 2600, 0.50, 0.02),
    ("gemini", "gemini-2.5-flash"): (0.08, 1500, 0.40, 0.05),
    ("ollama_local", "llama3.2:3b"): (0.12, 3400, 0.60, 0.02),
}

INTENTS = {"information": 0.50, "api_query": 0.14, "automation": 0.12, "chat": 0.12, "skill": 0.12}
SKILLS = {
    "information": ["llm_chat", "web_search"], "api_query": ["api_registry"],
    "automation": ["browser", "app_launcher"], "chat": ["llm_chat"], "skill": ["schedule", "expenses", "canvas"],
}
LLM_SKILLS = {"llm_chat", "web_search", "api_registry"}
ERRORS = [
    "APITimeoutError: Request timed out.",
    "Error code: 429 - {'type': 'rate_limit_error'}",
    "Error code: 500 - {'type': 'api_error'}",
    "ConnectionError: connection reset by peer",
]
WORDS = ("hola como estas necesito saber cuanto gaste este mes y que eventos tengo mañana "
         "busca precio del dolar abri spotify recordame pagar la tarjeta explicame docker").split()
IMPORTANCE = {"trivial": 0.35, "moderate": 0.35, "important": 0.10, "critical": 0.20}

_SCHEMAS = {
    "api_usage.db": """
        CREATE TABLE IF NOT EXISTS api_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, provider TEXT NOT NULL,
            model TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER, latency_ms REAL,
            cost_usd REAL, success INTEGER, error TEXT);""",
    "task_envelopes.db": """
        CREATE TABLE IF NOT EXISTS task_envelopes (
            request_id TEXT PRIMARY KEY, user_input TEXT, intent TEXT, steps_json TEXT,
            final_response TEXT, success INTEGER, duration_ms REAL, complexity_score REAL,
            capability_mismatch INTEGER, user_feedback TEXT, started_at REAL, finished_at REAL,
            channel_type TEXT, user_name TEXT, metadata_json TEXT, total_cost_usd REAL,
            total_tokens INTEGER);""",
    "conversation_history.db": """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, conversation_title TEXT,
            user_role TEXT, user_name TEXT, timestamp TEXT, user_input TEXT,
            assistant_response TEXT, importance_level TEXT, importance_score REAL,
            importance_reasoning TEXT, saved_to_memory INTEGER, manually_marked INTEGER,
            intent TEXT, complexity TEXT, elapsed_time REAL, mode_used TEXT, metadata TEXT,
            message_count INTEGER);""",
}
_META = "CREATE TABLE IF NOT EXISTS _synthetic_meta (key TEXT PRIMARY KEY, value TEXT);"


def _pick(rng: random.Random, weights: dict):
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _sentence(rng: random.Random, lo: int, hi: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(lo, hi)))


def _requests_for(day: date, rng: random.Random) -> int:
    # Busier on weekends, with day-to-day noise.
    base = 26 if day.weekday() >= 5 else 18
    return max(3, int(rng.gauss(base, 5)))


def _hour(rng: random.Random) -> float:
    # Two peaks: late morning and evening.
    return min(23.99, max(7.0, rng.gauss(11, 1.5) if rng.random() < 0.4 else rng.gauss(20, 2)))


def _llm_call(rng: random.Random, ts: float) -> dict:
    (provider, model) = _pick(rng, {k: v[0] for k, v in MODELS.items()})
    _, median, sigma, err_rate = MODELS[(provider, model)]
    tokens_in = int(rng.lognormvariate(math.log(900), 0.6))
    tokens_out = int(rng.lognormvariate(math.log(220), 0.7))
    failed = rng.random() < err_rate
    p_in, p_out = PRICES[(provider, model)]
    return {
        "ts": ts, "provider": provider, "model": model,
        "tokens_in": 0 if failed else tokens_in, "tokens_out": 0 if failed else tokens_out,
        "latency_ms": round(rng.lognormvariate(math.log(median), sigma), 1),
        "cost_usd": 0.0 if failed else round((tokens_in * p_in + tokens_out * p_out) / 1e6, 8),
        "success": 0 if failed else 1, "error": rng.choice(ERRORS) if failed else None,
    }


def _generate_day(day: date, seed: int, cons: dict[str, sqlite3.Connection]) -> None:
    rng = random.Random(f"{seed}-{day.isoformat()}")
    session = uuid.UUID(int=rng.getrandbits(128)).hex[:16]
    for _ in range(_requests_for(day, rng)):
        start = datetime.combine(day, time(), tzinfo=timezone.utc) + timedelta(hours=_hour(rng))
        ts = start.timestamp()
        intent = _pick(rng, INTENTS)
        steps, total_cost, total_tokens, elapsed = [], 0.0, 0, 0.0
        for skill in rng.sample(SKILLS[intent], k=rng.randint(1, len(SKILLS[intent]))):
            if skill in LLM_SKILLS:
                call = _llm_call(rng, ts + elapsed / 1000)
                cons["api_usage.db"].execute(
                    "INSERT INTO api_calls (ts, provider, model, tokens_in, tokens_out, latency_ms, cost_usd, success, error) "
                    "VALUES (:ts, :provider, :model, :tokens_in, :tokens_out, :latency_ms, :cost_usd, :success, :error)", call)
                dur, ok = call["latency_ms"], bool(call["success"])
                provider, model, err = call["provider"], call["model"], call["error"]
                t_in, t_out, cost = call["tokens_in"], call["tokens_out"], call["cost_usd"]
            else:
                dur, ok = round(rng.lognormvariate(math.log(700), 0.8), 1), rng.random() > 0.06
                provider = model = None
                err = None if ok else rng.choice(["TimeoutError: page did not load", "ValueError: no match"])
                t_in = t_out = 0
                cost = 0.0
            steps.append({"skill_name": skill, "args": {"input": _sentence(rng, 3, 9)},
                          "result_summary": _sentence(rng, 5, 20), "success": ok, "duration_ms": dur,
                          "provider": provider, "model": model, "tokens_in": t_in, "tokens_out": t_out,
                          "cost_usd": cost, "error": err})
            elapsed += dur
            total_cost += cost
            total_tokens += t_in + t_out
        # Routing, context building and sanitizing happen outside any step.
        elapsed += rng.lognormvariate(math.log(350), 0.5)
        success = all(s["success"] for s in steps)
        channel = "telegram" if rng.random() < 0.15 else "websocket"
        request_id = uuid.UUID(int=rng.getrandbits(128)).hex[:16]
        cons["task_envelopes.db"].execute(
            "INSERT INTO task_envelopes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (request_id, _sentence(rng, 3, 25), intent, json.dumps(steps, ensure_ascii=False),
             _sentence(rng, 5, 80), int(success), round(elapsed, 1), round(rng.random(), 4),
             int(rng.random() < 0.04), rng.choice([None] * 18 + ["positive", "negative"]),
             ts, ts + elapsed / 1000, channel, "demo_user", "{}", round(total_cost, 8), total_tokens))
        if channel == "websocket":
            level = _pick(rng, IMPORTANCE)
            cons["conversation_history.db"].execute(
                "INSERT INTO conversations (session_id, conversation_title, user_role, user_name, timestamp, "
                "user_input, assistant_response, importance_level, importance_score, importance_reasoning, "
                "saved_to_memory, manually_marked, intent, complexity, elapsed_time, mode_used, metadata, message_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (session, _sentence(rng, 2, 5), "admin", "demo_user",
                 start.replace(tzinfo=None).isoformat(), _sentence(rng, 3, 25), _sentence(rng, 5, 80), level,
                 {"trivial": 0.2, "moderate": 0.5, "important": 0.8, "critical": 1.0}[level],
                 _sentence(rng, 4, 10), int(level != "trivial"), int(rng.random() < 0.03), intent,
                 rng.choice(["simple", "complex"]), round(elapsed / 1000, 3), rng.choice(["auto", "cloud"]),
                 None, 1))


def ensure_until(out_dir: Path, end: date, days_back: int = 60, seed: int = 7) -> list[date]:
    """Create the stores if missing and append every day up to `end` (inclusive)
    that is not generated yet. Returns the days added."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cons = {name: sqlite3.connect(out_dir / name) for name in _SCHEMAS}
    try:
        for name, ddl in _SCHEMAS.items():
            cons[name].execute(ddl)
        meta = cons["api_usage.db"]
        meta.execute(_META)
        row = meta.execute("SELECT value FROM _synthetic_meta WHERE key = 'last_day'").fetchone()
        first = date.fromisoformat(row[0]) + timedelta(days=1) if row else end - timedelta(days=days_back - 1)
        added = []
        day = first
        while day <= end:
            _generate_day(day, seed, cons)
            added.append(day)
            day += timedelta(days=1)
        if added:
            meta.execute("INSERT OR REPLACE INTO _synthetic_meta VALUES ('last_day', ?)", (end.isoformat(),))
        for con in cons.values():
            con.commit()
        return added
    finally:
        for con in cons.values():
            con.close()
