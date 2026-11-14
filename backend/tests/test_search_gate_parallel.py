"""The tool-loop search gate runs concurrently with the router escalation.

`_start_search_gate` launches `_tool_loop_needs_search` as a task so its LLM call
overlaps `_cloud_llm_escalate` (both depend only on the user input).
"""
import asyncio
import time

import pytest

from core import orchestrator as orch
from modules.integrations import api_registry


@pytest.fixture
def gate_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(orch, "HAS_SEARCH_TOOLS", True)
    monkeypatch.setattr(api_registry, "_tool_loop_search_heuristic", lambda q: None)
    return monkeypatch


def test_gate_overlaps_escalation(gate_env):
    async def slow_gate(q):
        await asyncio.sleep(0.3)
        return False

    gate_env.setattr(api_registry, "_tool_loop_needs_search", slow_gate)

    async def run():
        t0 = time.perf_counter()
        task = orch._start_search_gate("explicame algo ambiguo", force_local=False)
        assert task is not None
        await asyncio.sleep(0.3)  # stands in for _cloud_llm_escalate
        result = await task
        return result, time.perf_counter() - t0

    result, elapsed = asyncio.run(run())
    assert result is False
    assert elapsed < 0.5  # ~0.3s concurrent, not 0.6s back to back


def test_no_gate_when_heuristic_decides(gate_env):
    gate_env.setattr(api_registry, "_tool_loop_search_heuristic", lambda q: True)

    async def run():
        return orch._start_search_gate("últimas noticias", force_local=False)

    assert asyncio.run(run()) is None


@pytest.mark.parametrize("force_local,key,has_tools", [
    (True, "sk-ant-test", True),
    (False, "", True),
    (False, "sk-ant-test", False),
])
def test_no_gate_when_tool_loop_cannot_run(monkeypatch, force_local, key, has_tools):
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    monkeypatch.setattr(orch, "HAS_SEARCH_TOOLS", has_tools)

    async def run():
        return orch._start_search_gate("algo ambiguo", force_local=force_local)

    assert asyncio.run(run()) is None


def test_cancelled_or_failed_gate_is_silent(gate_env):
    async def failing_gate(q):
        raise RuntimeError("boom")

    gate_env.setattr(api_registry, "_tool_loop_needs_search", failing_gate)
    errors = []

    async def run():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda l, ctx: errors.append(ctx))
        failed = orch._start_search_gate("algo ambiguo", force_local=False)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert failed.done()
        del failed

        async def slow(q):
            await asyncio.sleep(5)
        gate_env.setattr(api_registry, "_tool_loop_needs_search", slow)
        cancelled = orch._start_search_gate("algo ambiguo", force_local=False)
        cancelled.cancel()
        await asyncio.sleep(0)
        assert cancelled.cancelled()

    asyncio.run(run())
    import gc
    gc.collect()
    assert errors == []


@pytest.mark.parametrize("query,expected", [
    ("últimas noticias de Argentina", True),
    ("¿qué es la fotosíntesis?", False),
    ("explicame cómo funciona la fotosíntesis", False),
])
def test_heuristic_matches_async_gate(query, expected):
    assert api_registry._tool_loop_search_heuristic(query) is expected
    assert asyncio.run(api_registry._tool_loop_needs_search(query)) is expected
