"""
El loop Hermes graba TaskEnvelopes con intent + steps reales.

Antes, el wrapper de web_app grababa intent="unknown" y steps=[] siempre, por
lo que successful_complex() (filtro len(steps)>=3) nunca devolvía nada y el
skill_proposer jamás proponía. Acá verificamos que _finalize_envelope arma un
envelope con intent real y steps, y que el dispatch del registry devuelve el
skill_name.
"""
import types
import pytest

from core.orchestrator import Orchestrator
from core.task_envelope import StepRecord
from core.request_context import RequestContext
from core.clarification_gate import make_skill_candidate, make_router_candidate


class _CapturingStore:
    def __init__(self):
        self.saved = []

    def save(self, env):
        self.saved.append(env)


def _orch_with_store():
    fake = types.SimpleNamespace()
    fake._envelope_store = _CapturingStore()
    fake._cc_for_envelope = None
    # console con print no-op
    fake.console = types.SimpleNamespace(print=lambda *a, **k: None)
    for meth in ("_finalize_envelope", "_step_from_candidate"):
        setattr(fake, meth, types.MethodType(getattr(Orchestrator, meth), fake))
    fake._step_from_candidate = Orchestrator._step_from_candidate  # staticmethod
    return fake


def _ctx():
    return RequestContext(user_role="admin", user_name="Bruno",
                          authenticated=True, session_id="t",
                          channel_type="websocket")


def test_finalize_envelope_records_intent_and_steps():
    orch = _orch_with_store()
    steps = [StepRecord(skill_name="schedule", result_summary="ok", duration_ms=10)]
    orch._finalize_envelope("agendá cálculo", _ctx(), intent="skill",
                            steps=steps, response="Evento agendado.", success=True)
    assert len(orch._envelope_store.saved) == 1
    env = orch._envelope_store.saved[0]
    assert env.intent == "skill"
    assert env.intent != "unknown"
    assert len(env.steps) == 1
    assert env.steps[0].skill_name == "schedule"
    assert env.final_response == "Evento agendado."
    assert env.user_name == "Bruno"


def test_finalize_envelope_multistep_passes_min_steps_filter():
    # Un envelope de 3 steps es lo que el proposer necesita (successful_complex
    # filtra len(steps) >= 3). Verificamos que se puede construir.
    orch = _orch_with_store()
    steps = [
        StepRecord(skill_name="schedule", duration_ms=5),
        StepRecord(skill_name="PLAY_MEDIA", duration_ms=5),
        StepRecord(skill_name="automation", duration_ms=5),
    ]
    orch._finalize_envelope("multi", _ctx(), intent="multi_task",
                            steps=steps, response="done", success=True)
    env = orch._envelope_store.saved[0]
    assert len(env.steps) >= 3
    assert env.intent == "multi_task"


def test_finalize_envelope_no_store_is_safe():
    fake = types.SimpleNamespace()
    fake._envelope_store = None
    fake.console = types.SimpleNamespace(print=lambda *a, **k: None)
    fake._finalize_envelope = types.MethodType(Orchestrator._finalize_envelope, fake)
    # No debe romper si no hay store inyectado.
    fake._finalize_envelope("x", _ctx(), intent="skill", steps=[], response="r")


def test_step_from_candidate_skill():
    cand = make_skill_candidate(types.SimpleNamespace(name="schedule", description="agenda"), 1.0)
    step = Orchestrator._step_from_candidate("agendá X", cand, "Evento agendado.", 42)
    assert step.skill_name == "schedule"
    assert step.duration_ms == 42
    assert step.success is True


def test_step_from_candidate_router_carries_provider():
    analysis = {"automation_intent": "PLAY_MEDIA", "target": "lofi", "provider": "youtube"}
    cand = make_router_candidate(analysis, 0.6)
    step = Orchestrator._step_from_candidate("poné lofi", cand, "ok", 30)
    assert step.skill_name == "PLAY_MEDIA"
    assert step.provider == "youtube"


@pytest.mark.asyncio
async def test_registry_dispatch_returns_skill_name():
    from skills.registry import SkillRegistry, DispatchResult

    class _Skill:
        name = "demo"
        enabled = True
        metadata = types.SimpleNamespace(triggers=["demo"])

        def matches_command(self, ui):
            return ui.lower().startswith("demo")

        async def handle(self, ui, ctx):
            return "handled"

    reg = SkillRegistry()
    reg.register(_Skill())
    dr = await reg.dispatch("demo hola", _ctx())
    assert isinstance(dr, DispatchResult)
    assert dr.skill_name == "demo"
    assert dr.result == "handled"
    # try_handle sigue devolviendo solo el string (backward compat)
    assert await reg.try_handle("demo hola", _ctx()) == "handled"
    assert await reg.dispatch("nomatch", _ctx()) is None
