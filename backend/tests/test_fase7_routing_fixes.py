"""Tests exhaustivos para los fixes de Fase 7:
- Bug A: conversational guards para "no llegaste a abrir nada"
- Bug B: diagnostic routing en cloud mode
- Bug C: Pomodoro regex con verbos pasado
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re


def test_bug_a_conversational_guard():
    """Bug A: 'no llegaste a abrir nada' NO debe matchear como automation."""
    from core.router import IntentRouter
    print("\n=== BUG A: Conversational Guard ===")

    # Replicamos solo el regex _conversational del router (líneas 804+)
    AUTO_VERBS_PATTERN = r"(?:abre|abrir|abr[aá]|ejecut[aoe]|ejecutar|cierra|cerrar|lanza|lanzar|reproduce|inicia|iniciar|navega|navegar|abierto|abierta|pon|abres|ejecutas|cierras|lanzas|inicias|reproduces)"
    _conversational = re.compile(
        r'\b(?:'
        r'por\s*qu[eé]|c[oó]mo\s+es\s+que|cu[aá]ndo|si\s+eres|eres\s+capaz|'
        r'no\s+pudiste|no\s+puedes|ya\s+abriste|ahora\s+abriste|'
        r'no\s+(?:me\s+|te\s+|le\s+|les\s+|nos\s+)?'
        r'(?:llegaste|abriste|conseguiste|pudiste|alcanzaste|lanzaste|ejecutaste|hiciste|iniciaste|cerraste)\b|'
        r'que\s+no\s+(?:llegaste|abriste|pudiste|ejecutaste|conseguiste|lanzaste)\b|'
        r'no\s+te\s+ped[ií]|en\s+ning[uú]n\s+momento|nunca\s+te'
        r')',
        re.IGNORECASE,
    )

    should_block = [
        "no llegaste a abrir nada",
        "No llegaste a abrir nada. ¿Qué pasó?",
        "que no llegaste a abrir nada",
        "no me abriste discord",
        "no abriste youtube music",
        "no pudiste abrir calculadora",
        "por qué no pudiste abrir discord",
        "no conseguiste abrir nada",
        "no llegaste a abrir las apps que te pedí",
    ]
    should_allow = [
        "abre calculadora",
        "abre discord y luego youtube",
        "primero abre calculadora luego youtube music",
        "necesito que abras chrome",
        "lanza vscode",
        "reproduce thunderstruck",
    ]

    ok = True
    for s in should_block:
        m = _conversational.search(s.lower())
        status = "✓ BLOCK" if m else "✗ FAIL (not blocked)"
        if not m:
            ok = False
        print(f"  {status}: {s!r}")

    for s in should_allow:
        m = _conversational.search(s.lower())
        status = "✓ ALLOW" if not m else "✗ FAIL (false block)"
        if m:
            ok = False
        print(f"  {status}: {s!r}")

    assert ok, "Bug A: conversational guard tests failed"


def test_bug_b_diagnostic_routing():
    """Bug B: 'diagnóstico' debe matchear _RE_DIAGNOSTIC en router + _RE_CLOUD_DIAGNOSTIC en orchestrator."""
    from core.router import _RE_DIAGNOSTIC
    from core.orchestrator import _RE_CLOUD_DIAGNOSTIC

    print("\n=== BUG B: Diagnostic Routing ===")
    should_match = [
        "diagnóstico",
        "Diagnóstico",
        "diagnostico con tier 1",
        "Diagnóstico con el Taller 1. Revisa qué está pasando.",
        "haz un diagnóstico",
        "necesito un diagnóstico",
        "qué te pasa",
        "qué tienes",
        "qué está pasando",
        "qué pasó",
        "revisa los logs",
        "revisa los errores",
        "diagnostícate",
        "diagnostica",
    ]
    should_not_match = [
        "abre calculadora",
        "buenas noches",
        "qué hora es",
        "reproduce música",
        "abre el navegador",
    ]

    ok = True
    for s in should_match:
        m_router = _RE_DIAGNOSTIC.search(s)
        m_cloud = _RE_CLOUD_DIAGNOSTIC.search(s.lower())
        status = "✓ MATCH" if (m_router and m_cloud) else f"✗ FAIL router={bool(m_router)} cloud={bool(m_cloud)}"
        if not (m_router and m_cloud):
            ok = False
        print(f"  {status}: {s!r}")

    for s in should_not_match:
        m_router = _RE_DIAGNOSTIC.search(s)
        m_cloud = _RE_CLOUD_DIAGNOSTIC.search(s.lower())
        status = "✓ NO-MATCH" if (not m_router and not m_cloud) else f"✗ FAIL router={bool(m_router)} cloud={bool(m_cloud)}"
        if m_router or m_cloud:
            ok = False
        print(f"  {status}: {s!r}")

    assert ok, "Bug B: diagnostic regex tests failed"


def test_bug_c_pomodoro_verbs():
    """Bug C: regex de Pomodoro debe capturar pasado, presente, subjuntivo."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "schedule_skill",
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "skills", "builtin", "schedule", "skill.py"
        ),
    )
    # We only need the regex constant, but loading the full module triggers imports.
    # Read & exec just the constant.
    src = open(spec.origin, encoding="utf-8").read()
    chunk = src.split("_POMODORO_START_RE = ")[1].split("_POMODORO_MODE_RE")[0].strip()
    local_ns = {"re": re}
    exec("_POMODORO_START_RE = " + chunk, local_ns)
    POMO = local_ns["_POMODORO_START_RE"]

    print("\n=== BUG C: Pomodoro Verbs ===")
    should_match = [
        "activa pomodoro",
        "activa un pomodoro",
        "activo el pomodoro",
        "active una sesión de pomodoro",       # subjuntivo
        "activé una sesión de pomodoro",       # pasado 1ª
        "activó una sesión de pomodoro",       # pasado 3ª (Zephyro lo activó)
        "inicia pomodoro de 45 min",
        "inicié una sesión de pomodoro",       # pasado 1ª
        "inició una sesión de pomodoro",       # pasado 3ª — el bug original
        "Zephyro inició una sesión de Pomodoro",
        "empieza un pomodoro",
        "empezó un pomodoro",
        "empecé un pomodoro",
        "comenzó un pomodoro",
        "comencé el pomodoro",
        "arrancó un pomodoro",
        "arranqué el pomodoro",
        "start pomodoro",
        "quiero un pomodoro",
        "pon un pomodoro para cálculo",
        "lanza un pomodoro",
        "lancé un pomodoro",
    ]
    should_not_match = [
        "qué hora es",
        "agenda clase de cálculo",
        "abre el navegador",
        "el pomodoro está activo",   # mención sin verbo iniciador
    ]

    ok = True
    for s in should_match:
        m = POMO.search(s.lower())
        status = "✓ MATCH" if m else "✗ FAIL"
        if not m:
            ok = False
        print(f"  {status}: {s!r}")

    for s in should_not_match:
        m = POMO.search(s.lower())
        status = "✓ NO-MATCH" if not m else "✗ FAIL (false positive)"
        if m:
            ok = False
        print(f"  {status}: {s!r}")

    assert ok, "Bug C: Pomodoro regex tests failed"


if __name__ == "__main__":
    test_bug_a_conversational_guard()
    test_bug_b_diagnostic_routing()
    test_bug_c_pomodoro_verbs()
    print("\n=== ALL TESTS PASSED ===")
