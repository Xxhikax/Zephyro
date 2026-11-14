#!/usr/bin/env python3
"""
Qué DISPARA el DiagnosticAgent y qué lo ALIMENTA.

Tres regresiones reales, reportadas por el usuario en una sesión de call mode:

1. Falso positivo del trigger: «¿consideras que las funciones QUE TIENES son
   útiles…?» lanzaba un diagnóstico. El patrón de auto-reporte aceptaba `que`
   sin tilde, que en español es el pronombre RELATIVO, no el interrogativo.

2. Ruido como causa raíz: el WARNING «Edge memory critical» se emitía desde
   4500 MB (normal con muchas pestañas) y `living_state` lo levantaba como
   issue → el DiagnosticAgent lo servía como causa en CADA diagnóstico.

3. Más ruido: todo el stderr del MCP de GitNexus se logueaba como WARNING,
   incluido su banner de versión y sus logs de arranque (pino JSON level 30 =
   info) → «Encontré 4 errores» cuando no había ninguno.

Sin red. Importar browser_controller es seguro (no instancia BrowserConfig, que
haría CDP attach al Edge real — ver .claude/rules/tests-safety.md).
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock

import pytest

from core.orchestrator import _RE_CLOUD_DIAGNOSTIC
from core.router import _RE_DIAGNOSTIC

# (frase, debe_disparar_diagnóstico)
NO_DEBE_DISPARAR = [
    # El falso positivo original, textual.
    "Como asistente de IA, ¿consideras que las funciones que tienes ahorita mismo "
    "son útiles o crees que te falta algo más como una conexión adicional para que "
    "realmente pueda ser útil el día a día?",
    # Mismo relativo, más corto.
    "las herramientas que tienes son suficientes",
    "no sé que tienes en mente",
    "contame que tienes de nuevo en el proyecto",
    # Guards que ya existían y no deben romperse.
    "por qué está pasando esto en el país",
    "qué está pasando con las elecciones",
]

DEBE_DISPARAR = [
    # Auto-reporte con tilde (interrogativo).
    "¿qué te pasa?",
    "qué te pasa",
    "¿Qué tienes?",
    "qué está pasando",
    "no sé qué te pasa hoy",
    # Sin tilde pero en posición interrogativa (STT que no acentúa).
    "que te pasa",
    "¿que te pasa?",
    # Triggers explícitos.
    "haz un diagnóstico",
    "revisa los logs",
    "diagnostícate",
    "qué encontraste",
]


@pytest.mark.parametrize("frase", NO_DEBE_DISPARAR)
def test_no_dispara_diagnostico(frase):
    low = frase.lower()
    assert not _RE_DIAGNOSTIC.search(low), "router lo tomó como diagnóstico"
    assert not _RE_CLOUD_DIAGNOSTIC.search(low), "orchestrator lo tomó como diagnóstico"


@pytest.mark.parametrize("frase", DEBE_DISPARAR)
def test_si_dispara_diagnostico(frase):
    low = frase.lower()
    assert _RE_DIAGNOSTIC.search(low), "router NO lo tomó como diagnóstico"
    assert _RE_CLOUD_DIAGNOSTIC.search(low), "orchestrator NO lo tomó como diagnóstico"


@pytest.mark.parametrize("frase", NO_DEBE_DISPARAR + DEBE_DISPARAR)
def test_router_y_orchestrator_sincronizados(frase):
    """Los dos regex están duplicados a propósito (paths local y cloud): la misma
    frase DEBE clasificar igual en ambos, o el intent cambia según el modo."""
    low = frase.lower()
    assert bool(_RE_DIAGNOSTIC.search(low)) == bool(_RE_CLOUD_DIAGNOSTIC.search(low))


# ── Ruido 2: stderr del MCP de GitNexus ─────────────────────────────────────

@pytest.mark.parametrize("texto,esperado", [
    # Las tres líneas reales que aparecieron como "errores" en el diagnóstico.
    ("  GitNexus MCP (1.6.12)", logging.DEBUG),
    ('{"level":30,"time":1790484939330,"name":"gitnexus","repoCount":1,'
     '"msg":"GitNexus: MCP server starting"}', logging.DEBUG),
    ("", logging.DEBUG),
    # Lo que SÍ debe seguir subiendo.
    ('{"level":40,"msg":"index stale"}', logging.WARNING),
    ('{"level":50,"msg":"boom"}', logging.ERROR),
    ("Error: cannot open database", logging.ERROR),
    # JSON roto → no escalar.
    ("{malformado", logging.DEBUG),
    ("npm warn using --force", logging.DEBUG),
])
def test_gitnexus_stderr_level(texto, esperado):
    from modules.automation.gitnexus_bridge import _stderr_log_level
    assert _stderr_log_level(texto) == esperado


# ── Ruido 1: umbral de memoria de Edge ──────────────────────────────────────

def _make_monitor():
    """_HealthMonitor con un BrowserController falso (no toca Playwright/Edge)."""
    from modules.automation.browser_controller import BrowserController
    fake_browser = MagicMock()

    async def _set_health(*_a, **_kw):
        return None

    fake_browser._set_health = _set_health
    return BrowserController._HealthMonitor(fake_browser)


def _run_memory_check(monkeypatch, total_mb: float):
    """Corre _check_memory_usage con un psutil que reporta `total_mb` de Edge."""
    import modules.automation.browser_controller as bc

    proc = MagicMock()
    proc.info = {"pid": 1, "name": "msedge.exe", "memory_info": None}
    proc.memory_info.return_value = MagicMock(rss=total_mb * 1024 * 1024)

    fake_psutil = MagicMock()
    fake_psutil.process_iter.return_value = [proc]
    fake_psutil.NoSuchProcess = RuntimeError
    fake_psutil.AccessDenied = RuntimeError
    monkeypatch.setattr(bc, "psutil", fake_psutil)

    monitor = _make_monitor()
    asyncio.run(monitor._check_memory_usage())
    return monitor


@pytest.mark.parametrize("mb", [3600, 4600, 5900])
def test_memoria_normal_no_emite_warning(monkeypatch, caplog, mb):
    """Entre 3.5 y 6 GB no hay nada que corregir: sin WARNING, living_state no
    lo levanta como issue y no contamina el diagnóstico."""
    with caplog.at_level(logging.WARNING, logger="modules.automation.browser_controller"):
        monitor = _run_memory_check(monkeypatch, mb)
    assert "memory critical" not in caplog.text.lower(), f"emitió WARNING con {mb} MB"
    assert monitor._health_metrics["estimated_memory_mb"] == pytest.approx(mb, rel=1e-3)


def test_memoria_excesiva_si_emite_warning(monkeypatch, caplog):
    """Arriba de 6 GB sí degrada el navegador → el WARNING es legítimo."""
    with caplog.at_level(logging.WARNING, logger="modules.automation.browser_controller"):
        _run_memory_check(monkeypatch, 7837)  # el valor real del reporte
    assert "memory critical" in caplog.text.lower()
