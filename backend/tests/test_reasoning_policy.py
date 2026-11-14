"""Reasoning policy: per-turn effort decision and how it reaches the cloud request.

- pick_reasoning on the labeled dev set never goes BELOW the label (the costly
  error), except the known false-premise miss.
- LLMManager sends nothing new when there is no decision (byte-identical request).
- _decide_reasoning honours ZEPHYRO_REASONING_POLICY (off/shadow/on) and the THINK toggle.
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, _BACKEND)

from core.reasoning_policy import (  # noqa: E402
    KIND_LEVEL,
    POLICY_ENV,
    ReasoningDecision,
    pick_reasoning,
    policy_mode,
)

_DEV = Path(__file__).parent / "data" / "reasoning_eval.jsonl"
_ORDER = {"low": 0, "medium": 1, "high": 2}
# Prompts the regexes cannot reach. Each one is measured by bench_reasoning_effort.py.
_KNOWN_UNDER: set = set()


def _dev_rows():
    return [json.loads(line) for line in _DEV.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("row", _dev_rows(), ids=lambda r: r["id"])
def test_never_below_the_label_on_dev(row):
    if row["id"] in _KNOWN_UNDER:
        pytest.skip("known miss, measured by bench_reasoning_effort.py")
    decision = pick_reasoning(row["prompt"])
    assert _ORDER[decision.level] >= _ORDER[row["expected_level"]], (
        f"{row['id']}: {decision.level} ({decision.kind}: {decision.reason}) < {row['expected_level']}"
    )


def test_is_cheap():
    prompts = [r["prompt"] for r in _dev_rows()] * 25
    t0 = time.perf_counter()
    for p in prompts:
        pick_reasoning(p)
    assert (time.perf_counter() - t0) / len(prompts) < 0.001


@pytest.mark.parametrize("text,level", [
    ("hola, ¿todo bien?", "low"),
    ("gracias, nos vemos", "low"),
    ("¿quién escribió Rayuela?", "low"),
    ("escribí una función en python que ordene una lista", "high"),
    ("si hoy es lunes 26 de octubre que dia de la semana va a ser el 9 de julio", "high"),
    ("tengo 3 cajas y todas las etiquetas estan mal", "high"),
    ("compará python y go para un backend", "medium"),
    # Codex gpt-6.1-sol design review (2026-10-26): each was a reproduced miss.
    ("buenas todo bien una bomba carga un tanque en sesenta minutos y otra en ochenta minutos "
     "trabajan juntas veinte minutos cuanto tarda en completarse la carga", "high"),
    ("Por que Marie Curie recibio el Nobel por descubrir la penicilina?", "high"),
    ("Necesito una implementacion de fusionar_intervalos(xs) que maneje la lista vacia.", "high"),
    ("Escribime\nuna funcion para fusionar intervalos solapados.", "high"),
    ("Asigname guardias respetando disponibilidad, descansos y vacaciones.", "high"),
    ("Explicame Python y resolve esto: un precio sube 20% y luego baja 20%; termina en 9600. "
     "Cuanto valia antes?", "high"),
    ("Explicame este C#: public int Doble(int x) { return x * 2; }", "medium"),
    ("Manana tengo dos reuniones y estoy exactamente igual de nervioso que ayer. Deseame suerte.", "low"),
    ("Que significa dias habiles?", "low"),
    ("Escribime una postal para mi clase de yoga.", "low"),
    ("Armame un saludo para hoy lunes a las 8 h.", "low"),
    ("Cuanto es 2 mas 2?", "medium"),
])
def test_examples(text, level):
    assert pick_reasoning(text).level == level


def test_user_forced_is_high():
    d = pick_reasoning("hola", user_forced=True)
    assert (d.level, d.kind) == ("high", "forced")


def test_router_hints_lean_up_only():
    assert pick_reasoning("contame sobre los gatos", rich_hint="table").level == "medium"
    assert pick_reasoning("contame sobre los gatos", detail_level="detailed").level == "medium"


def test_every_kind_has_a_level():
    assert set(KIND_LEVEL.values()) <= {"low", "medium", "high"}


@pytest.mark.parametrize("raw,mode", [
    (None, "shadow"), ("on", "on"), ("OFF", "off"), (" shadow ", "shadow"), ("bogus", "shadow"),
])
def test_policy_mode(raw, mode):
    env = {} if raw is None else {POLICY_ENV: raw}
    with patch.dict(os.environ, env, clear=False):
        if raw is None:
            os.environ.pop(POLICY_ENV, None)
        assert policy_mode() == mode


# ── LLMManager translation ───────────────────────────────────────────────────

def _manager(provider):
    from core.llm_manager import LLMManager
    m = MagicMock(cloud_provider=provider)
    m._reasoning_kwargs = lambda r: LLMManager._reasoning_kwargs(m, r)
    return m


@pytest.mark.parametrize("provider,level,expected", [
    ("anthropic", None, {}),
    # Explicit even at high: Opus/Haiku default to a lower effort than Sonnet.
    ("anthropic", "high", {"output_config": {"effort": "high"}}),
    # Calibrated: Claude never goes below medium (same TTFT as low, better answers).
    ("anthropic", "low", {"output_config": {"effort": "medium"}}),
    ("anthropic", "medium", {"output_config": {"effort": "medium"}}),
    ("deepseek", "low", {}),
    ("deepseek", "medium", {}),
    ("deepseek", "high", {"extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}}),
    ("gemini", "low", {}),
])
def test_reasoning_kwargs(provider, level, expected):
    from core.llm_manager import LLMManager
    decision = None if level is None else ReasoningDecision(level, "test", "test")
    assert LLMManager._reasoning_kwargs(_manager(provider), decision) == expected


def test_generate_without_decision_sends_the_same_request():
    from core.llm_manager import LLMManager
    m = _manager("anthropic")
    m.cloud_model.invoke.return_value = MagicMock(content="ok")
    m._clean_response = lambda r: r.content
    LLMManager.generate(m, "hola", force_cloud=True)
    assert m.cloud_model.invoke.call_args.args == ("hola",)
    assert m.cloud_model.invoke.call_args.kwargs == {}


def test_generate_with_decision_sets_effort():
    from core.llm_manager import LLMManager
    m = _manager("anthropic")
    m.cloud_model.invoke.return_value = MagicMock(content="ok")
    m._clean_response = lambda r: r.content
    LLMManager.generate(m, "hola", force_cloud=True, reasoning=ReasoningDecision("low", "chat", "t"))
    assert m.cloud_model.invoke.call_args.kwargs == {"output_config": {"effort": "medium"}}


@pytest.mark.parametrize("level,expected", [
    (None, None), ("high", {"effort": "high"}), ("medium", {"effort": "medium"}), ("low", {"effort": "medium"}),
])
def test_generate_with_tools_effort(level, expected):
    from core.llm_manager import LLMManager
    resp = MagicMock(stop_reason="end_turn", content=[MagicMock(text="listo")])
    client = MagicMock()
    client.messages.create.return_value = resp
    decision = None if level is None else ReasoningDecision(level, "t", "t")

    async def _noop(name, inp):
        return ""

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        with patch("anthropic.Anthropic", return_value=client):
            LLMManager.generate_with_tools(
                MagicMock(anthropic_model="claude-sonnet-5-5", cloud_provider="deepseek"),
                system_prompt="s", user_message="u", tools=[{"name": "search"}],
                tool_executor=_noop, reasoning=decision,
            )
    assert client.messages.create.call_args.kwargs.get("output_config") == expected


def test_stream_fallback_back_to_cloud_keeps_the_decision():
    """Cloud stream fails before any token and there is no Ollama: generate() goes
    back to the cloud and must still get the decision (Codex review, 2026-10-26)."""
    from core.llm_manager import LLMManager
    m = _manager("deepseek")
    m.ollama_available = False
    m.is_online = True
    m.cloud_model.stream.side_effect = RuntimeError("stream down")
    m.generate.return_value = "full answer"
    decision = ReasoningDecision("high", "quant", "t")
    out = list(LLMManager.generate_stream(m, "p", force_cloud=True, reasoning=decision))
    assert out == ["full answer"]
    assert m.generate.call_args.kwargs == {"force_local": True, "reasoning": decision}


# ── orchestrator gate ────────────────────────────────────────────────────────

def _decide(mode, analysis=None, forced=False, text="¿cuántos números entre 1 y 1000 son divisibles por 3?"):
    from core.orchestrator import _decide_reasoning
    with patch.dict(os.environ, {POLICY_ENV: mode}):
        return _decide_reasoning(text, analysis or {"intent": "chat"}, "simple", user_forced=forced)


def test_decide_off_shadow_on():
    assert _decide("off") is None
    assert _decide("shadow") is None
    assert _decide("on").level == "high"


def test_decide_think_toggle_applies_in_shadow_but_not_off():
    assert _decide("shadow", forced=True, text="hola").kind == "forced"
    assert _decide("off", forced=True, text="hola") is None


@pytest.mark.parametrize("analysis", [{"intent": "chat", "fast_path": True}, {"intent": "automation"},
                                      {"intent": "skill"}])
def test_decide_skips_turns_without_a_chat_llm_call(analysis):
    assert _decide("on", analysis=analysis) is None


def test_decide_never_raises():
    with patch("core.reasoning_policy.pick_reasoning", side_effect=RuntimeError("boom")):
        assert _decide("on") is None
