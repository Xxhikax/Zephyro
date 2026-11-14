"""Verifica la robustez del manejo de started_at."""

import time
import pytest
from datetime import datetime, timezone


def test_started_at_is_float():
    """El default_factory de started_at debe generar float."""
    from core.task_envelope import TaskEnvelope
    env = TaskEnvelope(request_id="test-001", user_input="hola")
    assert isinstance(env.started_at, float), \
        f"started_at debe ser float, es {type(env.started_at)}"
    assert env.started_at > 0


def test_coerce_float_handles_all_types():
    """_coerce_float debe manejar datetime, string, float, int."""
    from core.task_envelope import _coerce_float

    now = time.time()

    # float
    assert _coerce_float(now) == now

    # int
    assert _coerce_float(int(now)) == float(int(now))

    # string (epoch float)
    assert _coerce_float(str(now)) == now

    # datetime con timestamp
    dt = datetime(2025, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    assert _coerce_float(dt) == dt.timestamp()

    # string ISO
    assert _coerce_float(dt.isoformat()) == dt.timestamp()

    # valores inválidos → 0.0
    assert _coerce_float("not_a_timestamp") == 0.0
    assert _coerce_float(None) == 0.0
    assert _coerce_float([1, 2, 3]) == 0.0
    assert _coerce_float({"key": "value"}) == 0.0


def test_from_row_coerces_started_at():
    """from_row debe convertir started_at a float."""
    from core.task_envelope import TaskEnvelope
    import sqlite3

    # Simular un row
    class MockRow(dict):
        def keys(self):
            return list(super().keys())

    row = MockRow({
        "request_id": "abc123",
        "user_input": "test",
        "intent": "chat",
        "steps_json": "[]",
        "final_response": "ok",
        "success": 1,
        "duration_ms": 100,
        "complexity_score": 0.5,
        "capability_mismatch": 0,
        "user_feedback": None,
        "started_at": 1746710400.0,
        "finished_at": 1746710401.0,
        "channel_type": "websocket",
        "user_name": None,
        "metadata_json": "{}",
    })

    env = TaskEnvelope.from_row(row)
    assert isinstance(env.started_at, float), \
        f"from_row debe producir float, produjo {type(env.started_at)}"
    assert env.started_at == 1746710400.0

    # También con string (legacy)
    row_str = MockRow({
        "request_id": "def456",
        "user_input": "test2",
        "intent": "chat",
        "steps_json": "[]",
        "final_response": "ok",
        "success": 1,
        "duration_ms": 100,
        "complexity_score": 0.5,
        "capability_mismatch": 0,
        "user_feedback": None,
        "started_at": "1746710400.0",
        "finished_at": "1746710401.0",
        "channel_type": "websocket",
        "user_name": None,
        "metadata_json": "{}",
    })

    env2 = TaskEnvelope.from_row(row_str)
    assert isinstance(env2.started_at, float), \
        f"String started_at debe convertirse a float, produjo {type(env2.started_at)}"
    assert env2.started_at == 1746710400.0


def test_started_at_comparison_always_works():
    """started_at >= since debe funcionar incluso con datos legacy."""
    from core.task_envelope import _coerce_float

    # Simular un envelope con started_at legacy string
    class FakeEnvelope:
        started_at = "1746710000.0"

    # Antes fallaba con TypeError: '>=' not supported
    since = time.time() - 3600
    result = _coerce_float(FakeEnvelope.started_at) >= since
    assert isinstance(result, bool), "La comparación debe producir un bool"
