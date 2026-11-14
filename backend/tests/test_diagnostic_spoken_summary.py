"""
Tests del resumen hablado del diagnóstico (call mode).

Bug origen (sesión call-mode 2026-06-12): al completarse un diagnóstico en
modo llamada, el usuario no escuchaba nada — el resultado solo aparecía en
el panel, y el mensaje de cierre mostraba el enum interno (more_context/view)
sin la causa raíz.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subagents.diagnostic_agent import _extract_spoken_summary  # noqa: E402


def test_spoken_summary_prioritizes_causa():
    summary = (
        "Diagnóstico completado (Tier 2, confianza 82%). "
        "Causa: el BrowserController perdió la conexión CDP con Edge"
    )
    spoken = _extract_spoken_summary(summary)
    assert spoken.startswith("Diagnóstico completado. La causa:")
    assert "perdió la conexión CDP" in spoken
    # Los metadatos tier/confianza no se hablan
    assert "Tier" not in spoken
    assert "confianza" not in spoken


def test_spoken_summary_passthrough_without_causa():
    assert _extract_spoken_summary("Diagnóstico ignorado.") == "Diagnóstico ignorado."
    assert _extract_spoken_summary("El diagnóstico falló: timeout") == (
        "El diagnóstico falló: timeout"
    )


def test_spoken_summary_empty_and_edge_cases():
    assert _extract_spoken_summary("") == ""
    # "Causa:" presente pero vacía → passthrough, no frase rota
    assert _extract_spoken_summary("Completado. Causa:") == "Completado. Causa:"


def test_completion_string_has_no_emojis_and_includes_causa():
    """El string de cierre de _handle_approval_action debe ser TTS-friendly."""
    import inspect
    from subagents import diagnostic_agent

    source = inspect.getsource(diagnostic_agent.DiagnosticAgent._handle_approval_action)
    assert "✅" not in source and "❌" not in source, (
        "Los strings de cierre no deben llevar emojis (se sintetizan por TTS)"
    )
    assert "Causa:" in source, "El cierre debe incluir la causa de la hipótesis"
