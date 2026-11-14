"""Tests for the scheduled-reflection data path.

Covers the bug where web_app's reflection job called
ContextManager.get_recent_conversations(), which did not exist —
the 6h reflection silently failed with AttributeError since then.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context_manager import ContextManager


def _cm(max_history: int = 50) -> ContextManager:
    return ContextManager(max_history=max_history, use_vector_memory=False)


def test_method_exists_for_reflection_job():
    # web_app.py calls context.get_recent_conversations(n=20)
    cm = _cm()
    assert callable(getattr(cm, "get_recent_conversations", None))


def test_empty_history_returns_empty_list():
    assert _cm().get_recent_conversations(n=20) == []


def test_pairs_user_and_assistant_turns():
    cm = _cm()
    cm.add("user", "hola, agenda gimnasio", skip_save=True)
    cm.add("assistant", "listo, agendado", skip_save=True)
    cm.add("user", "y para el viernes?", skip_save=True)
    cm.add("assistant", "el viernes tenés libre", skip_save=True)

    convs = cm.get_recent_conversations(n=20)
    assert convs == [
        {"user_input": "hola, agenda gimnasio", "response": "listo, agendado"},
        {"user_input": "y para el viernes?", "response": "el viernes tenés libre"},
    ]


def test_unpaired_turns_are_ignored():
    cm = _cm()
    cm.add("assistant", "respuesta huérfana", skip_save=True)
    cm.add("user", "pregunta sin respuesta", skip_save=True)
    assert cm.get_recent_conversations(n=20) == []


def test_n_limits_returned_pairs():
    cm = _cm()
    for i in range(10):
        cm.add("user", f"pregunta {i}", skip_save=True)
        cm.add("assistant", f"respuesta {i}", skip_save=True)
    convs = cm.get_recent_conversations(n=3)
    assert len(convs) == 3
    assert convs[-1] == {"user_input": "pregunta 9", "response": "respuesta 9"}


def test_shape_matches_reflection_engine_prompt_builder():
    # ReflectionEngine._build_prompt reads c["user_input"] / c["response"]
    cm = _cm()
    cm.add("user", "u", skip_save=True)
    cm.add("assistant", "r", skip_save=True)
    conv = cm.get_recent_conversations(n=1)[0]
    assert set(conv.keys()) == {"user_input", "response"}
