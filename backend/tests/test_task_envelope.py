"""Tests de TaskEnvelope + TaskEnvelopeStore + Scheduler."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import time

import pytest

from core.task_envelope import (
    StepRecord, TaskEnvelope, TaskEnvelopeStore, make_request_id,
)
from core.scheduler import ZephyroScheduler


@pytest.fixture
def store(tmp_path):
    return TaskEnvelopeStore(db_path=tmp_path / "envelopes.db")


# ── Envelope basics ─────────────────────────────────────────────────────

def test_make_request_id_unique():
    ids = {make_request_id() for _ in range(100)}
    assert len(ids) == 100


def test_envelope_total_cost_and_tokens():
    env = TaskEnvelope(request_id="r1", user_input="test")
    env.add_step(StepRecord(skill_name="a", tokens_in=100, tokens_out=50, cost_usd=0.001))
    env.add_step(StepRecord(skill_name="b", tokens_in=200, tokens_out=100, cost_usd=0.002))
    assert env.total_cost_usd() == pytest.approx(0.003)
    assert env.total_tokens() == 450


# ── Persistence ─────────────────────────────────────────────────────────

def test_save_and_get(store):
    env = TaskEnvelope(
        request_id="rid-1", user_input="¿qué hora es?",
        intent="chitchat", complexity_score=0.2, success=True,
    )
    env.add_step(StepRecord(
        skill_name="terminal", args={"cmd": "Get-Date"},
        result_summary="2026-04-30 12:00", success=True, duration_ms=120,
        provider="ollama_local", model="llama3.2:1b",
    ))
    store.save(env)

    loaded = store.get("rid-1")
    assert loaded is not None
    assert loaded.user_input == "¿qué hora es?"
    assert len(loaded.steps) == 1
    assert loaded.steps[0].skill_name == "terminal"
    assert loaded.steps[0].duration_ms == 120


def test_save_replaces_existing(store):
    env = TaskEnvelope(request_id="rid-2", user_input="v1")
    store.save(env)
    env.user_input = "v2"
    store.save(env)
    assert store.get("rid-2").user_input == "v2"


def test_recent_ordering(store):
    for i in range(5):
        env = TaskEnvelope(
            request_id=f"r{i}", user_input=f"q{i}",
            started_at=time.time() + i,
        )
        store.save(env)
    recent = store.recent(limit=3)
    assert len(recent) == 3
    assert recent[0].request_id == "r4"  # más reciente primero


def test_successful_complex_filter(store):
    # No matchea: no exitosa
    e1 = TaskEnvelope(request_id="x1", user_input="a", success=False, complexity_score=0.9)
    e1.steps = [StepRecord(skill_name="x")] * 5
    store.save(e1)

    # No matchea: complexity baja
    e2 = TaskEnvelope(request_id="x2", user_input="b", success=True, complexity_score=0.4)
    e2.steps = [StepRecord(skill_name="x")] * 5
    store.save(e2)

    # No matchea: pocos steps
    e3 = TaskEnvelope(request_id="x3", user_input="c", success=True, complexity_score=0.8)
    e3.steps = [StepRecord(skill_name="x")] * 2
    store.save(e3)

    # Sí matchea
    e4 = TaskEnvelope(request_id="x4", user_input="d", success=True, complexity_score=0.75)
    e4.steps = [StepRecord(skill_name="x")] * 4
    store.save(e4)

    matches = store.successful_complex(min_complexity=0.6, min_steps=3)
    ids = [e.request_id for e in matches]
    assert ids == ["x4"]


def test_usage_summary_aggregates(store):
    env = TaskEnvelope(request_id="u1", user_input="q", started_at=time.time())
    env.add_step(StepRecord(
        skill_name="introspection", provider="anthropic", model="claude-sonnet-4-6",
        tokens_in=1000, tokens_out=500, cost_usd=0.0105,
    ))
    env.add_step(StepRecord(
        skill_name="introspection", provider="anthropic", model="claude-sonnet-4-6",
        tokens_in=200, tokens_out=100, cost_usd=0.0021,
    ))
    env.add_step(StepRecord(
        skill_name="opencode", provider="deepseek", model="deepseek-v4-pro",
        tokens_in=500, tokens_out=300, cost_usd=0.000465,
    ))
    store.save(env)

    summary = store.usage_summary(since=time.time() - 3600)
    assert "anthropic/claude-sonnet-4-6" in summary
    assert summary["anthropic/claude-sonnet-4-6"]["calls"] == 2
    assert summary["anthropic/claude-sonnet-4-6"]["tokens_in"] == 1200
    assert summary["deepseek/deepseek-v4-pro"]["calls"] == 1


def test_cleanup_old_removes(store):
    very_old = TaskEnvelope(
        request_id="old", user_input="q",
        started_at=time.time() - (40 * 86400),
    )
    recent = TaskEnvelope(
        request_id="new", user_input="q", started_at=time.time(),
    )
    store.save(very_old)
    store.save(recent)

    removed = store.cleanup_old(max_age_days=30.0)
    assert removed == 1
    assert store.get("old") is None
    assert store.get("new") is not None


# ── Scheduler ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_runs_interval_job():
    sched = ZephyroScheduler()
    if sched.scheduler is None:
        pytest.skip("apscheduler no instalado")

    counter = {"v": 0}

    async def bump():
        counter["v"] += 1

    sched.add_interval("bump_test", bump, seconds=0.3)
    sched.start()
    try:
        await asyncio.sleep(1.0)
    finally:
        sched.shutdown()

    # Esperamos al menos 2-3 ejecuciones en 1 segundo
    assert counter["v"] >= 2


@pytest.mark.asyncio
async def test_scheduler_handles_sync_function():
    sched = ZephyroScheduler()
    if sched.scheduler is None:
        pytest.skip("apscheduler no instalado")
    counter = {"v": 0}
    sched.add_interval("sync_test", lambda: counter.__setitem__("v", counter["v"] + 1), seconds=0.3)
    sched.start()
    try:
        await asyncio.sleep(0.8)
    finally:
        sched.shutdown()
    assert counter["v"] >= 1


@pytest.mark.asyncio
async def test_scheduler_isolates_failures():
    sched = ZephyroScheduler()
    if sched.scheduler is None:
        pytest.skip("apscheduler no instalado")
    survived = {"good": 0}

    async def failing():
        raise RuntimeError("intentional")

    async def good():
        survived["good"] += 1

    sched.add_interval("bad_job", failing, seconds=0.3)
    sched.add_interval("good_job", good, seconds=0.3)
    sched.start()
    try:
        await asyncio.sleep(1.0)
    finally:
        sched.shutdown()
    # El job bueno sigue corriendo a pesar del fallo del otro
    assert survived["good"] >= 2


def test_list_jobs_empty():
    sched = ZephyroScheduler()
    if sched.scheduler is None:
        pytest.skip("apscheduler no instalado")
    assert sched.list_jobs() == []
