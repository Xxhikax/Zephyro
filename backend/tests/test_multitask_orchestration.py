"""
Tests del wiring de orquestación multi-tarea + clarificación multi-turno.

No instanciamos el Orchestrator completo (arrastra TTS/STT/visión). En su lugar
bindeamos los métodos reales a un objeto fake con las dependencias mockeadas
(skill_registry, router, automation, context). Eso prueba la lógica de
_route_one / _run_multitask / _resolve_pending_clarification de verdad.
"""
import types
import pytest

from core.orchestrator import Orchestrator
from core.request_context import RequestContext
from skills.registry import SkillCandidate


class _FakeSkill:
    def __init__(self, name, response):
        self.name = name
        self.description = f"skill {name}"
        self._response = response
        self.handled_with = None

    async def handle(self, user_input, ctx):
        self.handled_with = user_input
        return self._response


class _FakeRegistry:
    def __init__(self, ranking):
        # ranking: dict task_substring -> list[(skill, score)]
        self._ranking = ranking

    def rank_candidates(self, user_input, *, min_score=0.01):
        low = user_input.lower()
        for key, pairs in self._ranking.items():
            if key in low:
                return [SkillCandidate(skill=s, score=sc) for s, sc in pairs]
        return []


class _FakeRouter:
    def __init__(self, by_substring):
        self._by = by_substring

    async def classify_and_parse(self, task, skip_llm=False):
        low = task.lower()
        for key, analysis in self._by.items():
            if key in low:
                return dict(analysis)
        return {"intent": "chat", "_source": "fallback_information"}


class _FakeAutomation:
    def __init__(self):
        self.executed = []

    async def execute(self, analysis):
        self.executed.append(analysis)
        return f"automation:{analysis.get('automation_intent')}"


class _FakeContext:
    def __init__(self):
        self.entries = []

    def add(self, role, text):
        self.entries.append((role, text))


def _make_orch(registry, router, automation):
    fake = types.SimpleNamespace()
    fake.skill_registry = registry
    fake.router = router
    fake.automation = automation
    fake.context = _FakeContext()
    fake._pending_clarification = None
    fake._call_turn_buffer = None
    fake._envelope_store = None  # sin grabado en estos tests
    fake._cc_for_envelope = None
    fake.console = types.SimpleNamespace(print=lambda *a, **k: None)
    # Bindear los métodos reales del Orchestrator al fake.
    for meth in ("_route_one", "_execute_candidate", "_run_multitask",
                 "_resolve_pending_clarification", "_skill_action_checker",
                 "_finalize_envelope"):
        setattr(fake, meth, types.MethodType(getattr(Orchestrator, meth), fake))
    # staticmethods
    fake._match_clarification_reply = Orchestrator._match_clarification_reply
    fake._step_from_candidate = Orchestrator._step_from_candidate
    return fake


def _ctx():
    return RequestContext(
        user_role="admin", user_name="Bruno", authenticated=True, session_id="t",
    )


@pytest.mark.asyncio
async def test_multitask_two_clear_tasks_execute_in_order():
    sched = _FakeSkill("schedule", "Evento agendado.")
    registry = _FakeRegistry({"agend": [(sched, 1.0)], "música": []})
    router = _FakeRouter({
        "música": {"intent": "automation", "automation_intent": "PLAY_MEDIA",
                   "target": "lofi", "_source": "capa1_media_entities"},
    })
    automation = _FakeAutomation()
    orch = _make_orch(registry, router, automation)

    result = await orch._run_multitask(
        ["agendá cálculo el lunes", "poné música lofi"], _ctx()
    )
    assert "[1/2]" in result and "[2/2]" in result
    assert "Evento agendado" in result
    assert "automation:PLAY_MEDIA" in result
    assert sched.handled_with == "agendá cálculo el lunes"


@pytest.mark.asyncio
async def test_multitask_returns_none_when_any_subtask_is_not_command():
    """Caso real: «busca informacion sobre el modelo fable 5.1 y analiza el
    benchmark de este». El decomposer parte en 2 (ambos tienen verbo), pero
    CAPA 1.15 devuelve `information` (web search) y «analiza…» es chat: ninguno
    es skill/automation. Antes: «[1/2] No pude interpretar … como un comando».
    Ahora: None → handle_input procesa el mensaje ENTERO por el flujo single."""
    router = _FakeRouter({
        "busca informacion": {"intent": "information", "action": "web_search",
                              "needs_web_search": True},
        # «analiza el benchmark de este» → default del fake: intent chat
    })
    automation = _FakeAutomation()
    orch = _make_orch(_FakeRegistry({}), router, automation)

    result = await orch._run_multitask(
        ["busca informacion sobre el modelo fable 5.1", "analiza el benchmark de este"],
        _ctx(),
    )
    assert result is None
    assert automation.executed == []
    assert orch._pending_clarification is None
    # No dejó rastro en el contexto: el caller decide qué guardar.
    assert orch.context.entries == []


@pytest.mark.asyncio
async def test_multitask_returns_none_when_only_one_subtask_is_not_command():
    """Mixto: «agendá X y buscame info de Y» → el segmento de agenda resuelve,
    el de búsqueda informativa no → tampoco es multitarea (all-or-nothing)."""
    sched = _FakeSkill("schedule", "Evento agendado.")
    registry = _FakeRegistry({"agend": [(sched, 1.0)]})
    router = _FakeRouter({
        "info": {"intent": "information", "action": "web_search", "needs_web_search": True},
    })
    automation = _FakeAutomation()
    orch = _make_orch(registry, router, automation)

    result = await orch._run_multitask(
        ["agendá cálculo el lunes", "buscame info de fable 5.1"], _ctx()
    )
    assert result is None
    assert sched.handled_with is None  # no ejecutó el primer paso a medias
    assert automation.executed == []


@pytest.mark.asyncio
async def test_route_one_skips_llm_layer_in_cloud_mode():
    """En cloud mode el router debe recibir skip_llm=True (espeja handle_input)."""
    seen = {}

    class _SpyRouter(_FakeRouter):
        async def classify_and_parse(self, task, skip_llm=False):
            seen["skip_llm"] = skip_llm
            return await super().classify_and_parse(task, skip_llm=skip_llm)

    orch = _make_orch(_FakeRegistry({}), _SpyRouter({}), _FakeAutomation())
    ctx_cloud = RequestContext(user_role="admin", user_name="Bruno", authenticated=True,
                               session_id="t", force_mode="cloud")
    await orch._route_one("poné música", ctx_cloud)
    assert seen["skip_llm"] is True

    await orch._route_one("poné música", _ctx())
    assert seen["skip_llm"] is False


@pytest.mark.asyncio
async def test_multitask_ambiguity_asks_at_start_and_defers():
    # "poné algo" → schedule(0.45) vs PLAY_MEDIA(0.5): empate dentro del margen.
    sched = _FakeSkill("schedule", "Evento agendado.")
    registry = _FakeRegistry({"agend": [(sched, 1.0)], "algo": [(sched, 0.45)]})
    router = _FakeRouter({
        "algo": {"intent": "automation", "automation_intent": "PLAY_MEDIA",
                 "target": "algo", "_source": "capa2_haiku"},  # 0.6
    })
    automation = _FakeAutomation()
    orch = _make_orch(registry, router, automation)

    result = await orch._run_multitask(["agendá cálculo", "poné algo"], _ctx())
    assert "aclar" in result.lower()
    assert orch._pending_clarification is not None
    # No ejecutó nada todavía
    assert automation.executed == []
    assert sched.handled_with is None


@pytest.mark.asyncio
async def test_resolve_pending_executes_full_plan():
    sched = _FakeSkill("schedule", "Evento agendado.")
    play = {"intent": "automation", "automation_intent": "PLAY_MEDIA", "target": "lofi"}
    automation = _FakeAutomation()
    orch = _make_orch(_FakeRegistry({}), _FakeRouter({}), automation)

    from core.clarification_gate import make_router_candidate, make_skill_candidate
    opt_play = make_router_candidate(play, 0.6)
    opt_sched = make_skill_candidate(sched, 0.45)
    orch._pending_clarification = {
        "plan": [
            {"task": "agendá cálculo",
             "chosen": make_skill_candidate(sched, 1.0), "options": []},
            {"task": "poné algo", "chosen": None,
             "options": [opt_play, opt_sched]},
        ],
        "ts": __import__("time").time(),
        "original_input": "agendá cálculo y poné algo",
    }
    # Usuario responde "música" → mapea a PLAY_MEDIA
    result = await orch._resolve_pending_clarification("música", _ctx())
    assert result is not None
    assert "automation:PLAY_MEDIA" in result
    assert orch._pending_clarification is None


@pytest.mark.asyncio
async def test_resolve_pending_no_match_returns_none():
    automation = _FakeAutomation()
    orch = _make_orch(_FakeRegistry({}), _FakeRouter({}), automation)
    from core.clarification_gate import Candidate
    orch._pending_clarification = {
        "plan": [{"task": "poné algo", "chosen": None, "options": [
            Candidate(kind="router", score=0.6, phrase="reproduzca eso",
                      payload={}, label="PLAY_MEDIA")]}],
        "ts": __import__("time").time(),
        "original_input": "poné algo",
    }
    # Respuesta que no mapea a nada → None (se procesa como mensaje nuevo)
    result = await orch._resolve_pending_clarification("xyzqwerty", _ctx())
    assert result is None


def test_match_reply_by_position():
    from core.clarification_gate import Candidate
    opts = [
        Candidate(kind="skill", score=1.0, phrase="lo agende", label="schedule"),
        Candidate(kind="router", score=0.6, phrase="reproduzca eso", label="PLAY_MEDIA"),
    ]
    assert Orchestrator._match_clarification_reply("el primero", opts).label == "schedule"
    assert Orchestrator._match_clarification_reply("2", opts).label == "PLAY_MEDIA"


@pytest.mark.asyncio
async def test_resolve_pending_affirmative_single_option():
    # Caso real: agenda resuelta + música con 1 opción → "si porfavor" confirma.
    sched = _FakeSkill("schedule", "Evento agendado.")
    play = {"intent": "automation", "automation_intent": "PLAY_MEDIA", "target": "lofi"}
    automation = _FakeAutomation()
    orch = _make_orch(_FakeRegistry({}), _FakeRouter({}), automation)
    from core.clarification_gate import make_router_candidate, make_skill_candidate
    orch._pending_clarification = {
        "plan": [
            {"task": "agendá cálculo el lunes a las 7",
             "chosen": make_skill_candidate(sched, 1.0), "options": []},
            {"task": "poné lofi en youtube music", "chosen": None,
             "options": [make_router_candidate(play, 0.6)]},
        ],
        "ts": __import__("time").time(),
        "original_input": "agendá cálculo el lunes a las 7 y poné lofi en youtube music",
    }
    result = await orch._resolve_pending_clarification("si porfavor", _ctx())
    assert result is not None
    assert "Evento agendado" in result
    assert "automation:PLAY_MEDIA" in result
    assert orch._pending_clarification is None


@pytest.mark.asyncio
async def test_resolve_pending_negative_skips():
    sched = _FakeSkill("schedule", "Evento agendado.")
    play = {"intent": "automation", "automation_intent": "PLAY_MEDIA", "target": "lofi"}
    automation = _FakeAutomation()
    orch = _make_orch(_FakeRegistry({}), _FakeRouter({}), automation)
    from core.clarification_gate import make_router_candidate, make_skill_candidate
    orch._pending_clarification = {
        "plan": [
            {"task": "agendá cálculo", "chosen": make_skill_candidate(sched, 1.0), "options": []},
            {"task": "poné lofi", "chosen": None,
             "options": [make_router_candidate(play, 0.6)]},
        ],
        "ts": __import__("time").time(), "original_input": "x",
    }
    # "no" → omite la música, ejecuta la agenda
    result = await orch._resolve_pending_clarification("no", _ctx())
    assert result is not None
    assert "Evento agendado" in result
    assert "Omití" in result
    assert automation.executed == []  # música NO se ejecutó


def test_match_reply_affirmative_single_option():
    from core.clarification_gate import Candidate
    one = [Candidate(kind="router", score=0.6, phrase="reproduzca eso", label="PLAY_MEDIA")]
    assert Orchestrator._match_clarification_reply("si porfavor", one).label == "PLAY_MEDIA"
    assert Orchestrator._match_clarification_reply("dale", one).label == "PLAY_MEDIA"
    # Con DOS opciones, "sí" es ambiguo → no resuelve por afirmación
    two = one + [Candidate(kind="skill", score=0.5, phrase="lo agende", label="schedule")]
    assert Orchestrator._match_clarification_reply("sí", two) is None


def test_match_reply_by_keyword():
    from core.clarification_gate import Candidate
    opts = [
        Candidate(kind="skill", score=1.0, phrase="lo agende", label="schedule"),
        Candidate(kind="router", score=0.6, phrase="reproduzca eso", label="PLAY_MEDIA"),
    ]
    # "agendá" contiene "agend" → matchea "agende"
    assert Orchestrator._match_clarification_reply("agendalo", opts).label == "schedule"
