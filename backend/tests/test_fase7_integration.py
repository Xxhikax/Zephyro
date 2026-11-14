"""Test integrado: simula el flujo orchestrator → router para los 3 bugs de Fase 7."""
import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_orchestrator_needs_router_diagnostic():
    """El _needs_router del orchestrator debe disparar para queries de diagnostic."""
    from core.orchestrator import _RE_CLOUD_AUTOMATION, _RE_CLOUD_VISION, _RE_CLOUD_DIAGNOSTIC

    print("\n=== Integración: _needs_router diagnostic en cloud mode ===")
    inputs = [
        "Diagnóstico con el Taller 1. Revisa qué está pasando.",
        "haz un diagnóstico",
        "qué te pasa",
        "diagnostícate",
    ]
    ok = True
    for s in inputs:
        _uil = s.lower()
        triggers = bool(
            _RE_CLOUD_AUTOMATION.search(_uil)
            or _RE_CLOUD_VISION.search(_uil)
            or _RE_CLOUD_DIAGNOSTIC.search(_uil)
        )
        status = "✓ triggers router" if triggers else "✗ FAIL (router bypass)"
        if not triggers:
            ok = False
        print(f"  {status}: {s!r}")
    assert ok


async def test_router_classifies_diagnostic_intent():
    """El router debe asignar intent='diagnostic' para esos inputs."""
    from core.router import IntentRouter

    print("\n=== Integración: router.classify_and_parse intent=diagnostic ===")
    class _MockLLM:
        is_online = False
        async def generate(self, *a, **k): return ""
    router = IntentRouter(llm_manager=_MockLLM())
    inputs = [
        "Diagnóstico con el Taller 1. Revisa qué está pasando.",
        "haz un diagnóstico",
        "diagnostícate",
        "revisa los errores",
    ]
    ok = True
    for s in inputs:
        result = await router.classify_and_parse(s, skip_llm=True)
        intent = result.get("intent", "?")
        status = "✓" if intent == "diagnostic" else f"✗ FAIL got '{intent}'"
        if intent != "diagnostic":
            ok = False
        print(f"  {status}: {s!r} → intent={intent}")
    assert ok


async def test_pomodoro_skill_matches_past_tense():
    """El ScheduleSkill debe detectar Pomodoro start con verbos en pasado."""
    print("\n=== Integración: ScheduleSkill matches_command(Pomodoro pasado) ===")

    # Cargar el skill via el registry
    from pathlib import Path
    from skills.registry import SkillRegistry
    from skills.loader import SkillLoader

    registry = SkillRegistry()
    skill_dirs = [
        Path(__file__).parent.parent / "skills" / "builtin",
    ]
    loader = SkillLoader(skill_dirs)
    loader.load_into(registry)

    schedule_skill = registry.get_skill("schedule")
    assert schedule_skill is not None, "schedule skill no cargada"

    inputs = [
        "activa pomodoro",
        "Zephyro inició una sesión de Pomodoro",
        "active una sesión de Pomodoro",
        "empecé un pomodoro",
        # Fase 8: loose hint sin verbo (debe matchear vía _POMODORO_HINT_RE)
        "Una sesión de Pomodoro.",
        "una sesion de pomodoro",
        "pomodoro de 45 min",
        "modo pomodoro",
    ]
    ok = True
    for s in inputs:
        m = schedule_skill.matches_command(s, ctx=None)
        status = "✓" if m else "✗ FAIL"
        if not m:
            ok = False
        print(f"  {status}: {s!r} → matches={m}")
    assert ok


async def test_conversational_guard_blocks_router():
    """El router NO debe clasificar quejas conversacionales como automation."""
    from core.router import IntentRouter

    print("\n=== Integración: router NO clasifica quejas como automation ===")
    class _MockLLM:
        is_online = False
        async def generate(self, *a, **k): return ""
    router = IntentRouter(llm_manager=_MockLLM())
    inputs = [
        "No llegaste a abrir nada. ¿Qué pasó?",
        "que no llegaste a abrir nada",
        "no me abriste discord",
        "no abriste youtube music",
    ]
    ok = True
    for s in inputs:
        result = await router.classify_and_parse(s, skip_llm=True)
        intent = result.get("intent", "?")
        # No queremos automation; chat o diagnostic está bien
        status = "✓" if intent != "automation" else f"✗ FAIL got '{intent}'"
        if intent == "automation":
            ok = False
        print(f"  {status}: {s!r} → intent={intent}")
    assert ok


async def main():
    await test_orchestrator_needs_router_diagnostic()
    await test_router_classifies_diagnostic_intent()
    await test_pomodoro_skill_matches_past_tense()
    await test_conversational_guard_blocks_router()
    print("\n=== INTEGRATION TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
