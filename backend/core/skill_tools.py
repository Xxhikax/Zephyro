"""
skill_tools — Anthropic tool schemas para que Claude/LLM invoque skills
autónomamente (function-calling para introspección y self-diagnóstico).

Tools expuestos:
- diagnose_zephyro:  log_reader._diagnose() — analiza logs internos con Haiku
- introspect_code:   introspection skill — análisis tiered (GitNexus / OpenCode)
- read_project_file: file_ops — lee archivos del proyecto (con whitelist)

Estos tools se pasan a generate_with_tools() en orchestrator.py cuando el input
sugiere intent diagnóstica/introspectiva (palabras como "qué pasó", "investiga",
"cómo funciona", "dónde está").
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# ── Tool schemas (Anthropic format) ──────────────────────────────────────

DIAGNOSE_TOOL = {
    "name": "diagnose_zephyro",
    "description": (
        "Lee los logs internos de Zephyro y analiza qué pasó. "
        "USAR cuando el usuario pregunta 'qué pasó', 'por qué no funcionó', "
        "'investiga', 'cuál es el problema', 'revisa los logs'. "
        "Devuelve análisis estructurado con causa probable + evidencia (timestamps "
        "y líneas de log reales). NO inventes — usa este tool antes de especular."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Pregunta concreta sobre qué se quiere diagnosticar",
            },
            "lookback_seconds": {
                "type": "integer",
                "description": "Ventana de tiempo hacia atrás en segundos (default 300 = 5 min)",
                "default": 300,
            },
            "log_file": {
                "type": "string",
                "enum": ["backend.log", "living_state.log", "diagnostic_agent.log", "playwright.log"],
                "default": "backend.log",
                "description": "Archivo de log a inspeccionar",
            },
        },
        "required": ["question"],
    },
}


INTROSPECT_TOOL = {
    "name": "introspect_code",
    "description": (
        "Analiza el código fuente de Zephyro para responder preguntas sobre "
        "cómo funciona algo, dónde está definido un símbolo, o qué hace una "
        "función. Usa GitNexus (mapa estructural rápido) por default. "
        "USAR cuando el usuario pregunta 'cómo funciona X', 'dónde está Y', "
        "'qué hace la función Z' o necesitás entender el código antes de "
        "responder. NO inventes la respuesta — usa este tool primero."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Pregunta sobre el código (concepto, símbolo o función)",
            },
            "tier": {
                "type": "string",
                "enum": ["gitnexus", "opencode", "auto"],
                "default": "gitnexus",
                "description": (
                    "Tier de análisis: 'gitnexus' rápido (<1s, default), "
                    "'opencode' profundo (5-30s, cuando GitNexus no alcanza)"
                ),
            },
        },
        "required": ["query"],
    },
}


READ_FILE_TOOL = {
    "name": "read_project_file",
    "description": (
        "Lee un archivo del proyecto Zephyro. Whitelist: backend/**, src/**, "
        "tools/**, *.md, *.txt en raíz. BLOQUEADOS: .env, *_token*, *credentials*, "
        "paths con '..' o '~'. USAR cuando necesitás ver el contenido exacto "
        "de un archivo específico para responder con precisión."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relativo al root del proyecto (ej. 'backend/cli.py')",
            },
            "line_start": {
                "type": "integer",
                "description": "Primera línea a leer (1-indexed)",
                "default": 1,
            },
            "line_count": {
                "type": "integer",
                "description": "Cuántas líneas leer (máx 500)",
                "default": 200,
            },
        },
        "required": ["path"],
    },
}


# ── File access guard (read_project_file) ────────────────────────────────

# Repo root (backend/core/skill_tools.py -> two levels up), not a machine-specific path.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_BLOCKED_FILE_PATTERNS = (
    ".env",
    "_token",
    "credentials",
    "secret",
    "private_key",
    "id_rsa",
    ".pem",
)

_ALLOWED_TOP_DIRS = {"backend", "src", "tools", "scripts", "docs", "tests", ".claude"}


def is_path_allowed(rel_path: str) -> tuple[bool, str]:
    """
    Valida que `rel_path` sea seguro para leer.
    Devuelve (allowed: bool, reason: str).
    """
    if not rel_path or not isinstance(rel_path, str):
        return False, "path vacío o inválido"

    # Rechazar traversal y home references
    if ".." in rel_path or "~" in rel_path:
        return False, "path contiene '..' o '~'"

    # Path absoluto fuera del proyecto
    p = Path(rel_path.replace("\\", "/"))
    if p.is_absolute():
        try:
            resolved = p.resolve()
            if PROJECT_ROOT not in resolved.parents and resolved != PROJECT_ROOT:
                return False, "path absoluto fuera del proyecto"
        except Exception:
            return False, "path absoluto inválido"
    else:
        # Resolver contra PROJECT_ROOT y verificar que no escape
        try:
            resolved = (PROJECT_ROOT / p).resolve()
            if PROJECT_ROOT not in resolved.parents and resolved != PROJECT_ROOT:
                return False, "path escapa de PROJECT_ROOT"
        except Exception:
            return False, "path inválido"

    # Bloquear nombres sensibles
    name_lower = p.name.lower()
    for pattern in _BLOCKED_FILE_PATTERNS:
        if pattern in name_lower:
            return False, f"archivo bloqueado (patrón: '{pattern}')"

    # Verificar que el top-level dir esté en allowed (si hay subdir)
    parts = p.parts
    if len(parts) > 1:
        top = parts[0].lower()
        if top not in _ALLOWED_TOP_DIRS:
            # Permitir archivos sueltos en raíz si son .md/.txt
            return False, f"directorio top '{top}' no permitido"

    return True, "ok"


def all_skill_tools() -> list[dict[str, Any]]:
    """Lista de tool schemas listos para pasar a generate_with_tools()."""
    return [DIAGNOSE_TOOL, INTROSPECT_TOOL, READ_FILE_TOOL]
