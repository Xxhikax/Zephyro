"""LogReader Skill — lee los logs de Zephyro con filtros de seguridad.

Whitelist de archivos: solo logs en `logs/` y `backend/logs/`. NO permite
path traversal (.. , symlinks fuera de la whitelist).

Si el usuario pide diagnóstico (no solo logs crudos), usa Haiku para analizar
el contenido y generar una reflexión accionable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill

logger = logging.getLogger("zephyro.log_reader")


# Whitelist de archivos permitidos — rutas absolutas desde la ubicación de este archivo
_SKILL_FILE = Path(__file__).resolve()
_LOG_DIRS = (
    _SKILL_FILE.parents[4] / "logs",  # zephyro/logs (project root)
    _SKILL_FILE.parents[3] / "logs",  # backend/logs  (fallback)
)
_ALLOWED_NAMES = frozenset({
    "backend.log",
    "terminal.log",
    "diagnostic_agent.log",
    "living_state.log",
    "opencode.log",
    "file_ops.log",
})

_DEFAULT_TAIL_LINES = 50
_MAX_TAIL_LINES = 500
_ERROR_RE = re.compile(r"\b(?:ERROR|CRITICAL|FATAL|Traceback)\b")

# Patrones de ruido a filtrar en modo diagnóstico (Telegram polling, scheduler)
_NOISE_RE = re.compile(
    r"(?:httpx: HTTP Request: POST https://api\.telegram\.org/.*getUpdates|"
    r"apscheduler\.executors\.default:\s*(?:Running|executed successfully))"
)

# Patrones que indican que el usuario quiere diagnóstico, no solo logs crudos
_DIAGNOSTIC_HINT = re.compile(
    r"\b(?:qué\s+(?:fall[oó]|pas[oó]|sucedi[oó]|ocurri[oó])|"
    r"por\s+qu[eé]\s+(?:fall[oó]|no\s+funcion)|"
    r"diagnostic|diagn[oó]stic|analiza|analizar|diagnose|"
    r"revisa(?:\s+los?\s+logs?)?\s+para\s+ver\s+qu[eé]\s+(?:fall[oó]|pas[oó])|"
    r"revisa(?:\s+los?\s+logs?)?\s+del\s+sistema|"
    r"revisa\s+logs\s+del\s+sistema)",
    re.IGNORECASE,
)


def _parse_log_timestamp(line: str, file_date: object) -> float | None:
    """Parsea HH:MM:SS del inicio de línea y devuelve epoch timestamp.
    Usa file_date (datetime.date) como fecha base."""
    m = re.match(r'(\d{2}):(\d{2}):(\d{2})\b', line)
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    day_start = datetime(file_date.year, file_date.month, file_date.day)
    return (day_start.replace(hour=h, minute=mi, second=s).timestamp())


_DIAGNOSIS_PROMPT = """Eres un diagnosticador de sistemas. Analiza los siguientes logs de Zephyro (asistente IA personal) y genera un diagnóstico claro y accionable.

Contexto del usuario: "{user_question}"

Reglas:
1. Identifica el error raíz (NO solo describas el síntoma)
2. Explica por qué ocurrió en lenguaje simple (español)
3. Si el error es de código (traceback, excepción), señala el archivo y línea
4. Si el error es de configuración/conexión, explica qué falta o falla
5. Si no hay errores claros en los logs, dilo honestamente
6. Sugiere pasos concretos para resolverlo (o al menos investigar más)
7. Sé conciso: máximo 5-8 líneas de diagnóstico
8. NO repitas los logs textualmente — analízalos

Logs del sistema:
{log_content}

Responde en español, en formato:
**Diagnóstico:** <causa raíz>
**Archivo:** <archivo:línea si es error de código>
**Solución:** <pasos concretos>"""


_LOG_CMD_RE = re.compile(
    r"\b(?:ver|revisa?r?|verifica?r?|mu[eé]strame|dame|lee)\b"
    r"(?:\s+(?:los?|mis?|tus?|sus?|el|un))?"
    r"\s+logs?\b"
    r"|\bultimos\s+logs\b|\berrores\s+recientes\b|\bque\s+dicen\s+los\s+logs\b",
    re.IGNORECASE,
)


def _wants_diagnosis(user_input: str) -> bool:
    """Detecta si el usuario pide diagnóstico, no solo logs crudos."""
    return bool(_DIAGNOSTIC_HINT.search(user_input))


class LogReaderSkill(BaseSkill):

    def matches_command(self, user_input: str) -> bool:
        if super().matches_command(user_input):
            return True
        lower = user_input.lower().strip()
        # Frases sueltas con artículos/posesivos flexibles
        return bool(_LOG_CMD_RE.search(lower))

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        text = user_input.strip()
        lower = text.lower()

        # /logs request <id>
        m = re.search(r"/logs?\s+request\s+(\S+)", lower)
        if m:
            log_result = self._by_request_id(m.group(1))
            if _wants_diagnosis(text):
                return await self._diagnose(log_result, text)
            return log_result

        # /logs errors
        if "/logs errors" in lower or "errores recientes" in lower:
            log_result = self._errors(window_seconds=300)
            if _wants_diagnosis(text):
                return await self._diagnose(log_result, text)
            return log_result

        # /logs <archivo> [N]
        m = re.search(r"/logs?\s+(\S+\.log)(?:\s+(\d+))?", lower)
        if m:
            fname = m.group(1)
            n = int(m.group(2)) if m.group(2) else _DEFAULT_TAIL_LINES
            log_result = self._tail(fname, min(n, _MAX_TAIL_LINES))
            if _wants_diagnosis(text):
                return await self._diagnose(log_result, text)
            return log_result

        # /logs <arg> where arg is NOT a .log file — reject suspicious paths
        # (e.g. /logs /etc/passwd 10 or /logs ../../../etc/passwd 10)
        m_any = re.search(r"/logs?\s+(\S+)", lower)
        if m_any:
            raw_arg = m_any.group(1)
            # If it looks like a path (starts with / or ..) or is not a plain word,
            # reject it to prevent path traversal attempts.
            if raw_arg.startswith("/") or raw_arg.startswith("..") or os.sep in raw_arg:
                allowed = ", ".join(sorted(_ALLOWED_NAMES))
                return (
                    f"Argumento `{raw_arg}` no permitido. "
                    f"Solo se aceptan nombres de archivo de la whitelist: {allowed}."
                )

        # Default: diagnóstico usa _errors() primero; sin diagnóstico → tail
        if _wants_diagnosis(text):
            errors_result = self._errors(window_seconds=900)  # 15 min
            if "Sin errores" not in errors_result:
                return await self._diagnose(errors_result, text)
            # Fallback: sin errores recientes → mostrar últimas líneas + nota
            tail_result = self._tail("backend.log", _DEFAULT_TAIL_LINES)
            log_result = (
                "(sin errores recientes — mostrando últimas líneas)\n\n" + tail_result
            )
            if self._has_errors(log_result):
                return await self._diagnose(log_result, text)
            return log_result

        log_result = self._tail("backend.log", _DEFAULT_TAIL_LINES)
        return log_result

    # ── Diagnosis ─────────────────────────────────────────────────────

    @staticmethod
    def _has_errors(log_content: str) -> bool:
        """Verifica si el contenido de logs contiene errores reales."""
        return bool(re.search(r'\b(?:ERROR|CRITICAL|FATAL|Traceback|Exception|fail)\b', log_content))

    async def _diagnose(self, log_content: str, user_question: str) -> str:
        """Usa Haiku para generar un diagnóstico a partir de los logs."""
        # Si no hay errores en los logs, no invocar LLM
        if not self._has_errors(log_content):
            return log_content + "\n\n✓ No se detectaron errores en los logs revisados."

        # Intentar usar llm_manager inyectado; fallback al método antiguo
        llm_manager = getattr(self, 'llm_manager', None)
        if llm_manager is None:
            try:
                import web_app as _wa
                orch = _wa.app.state.orchestrator if hasattr(_wa, 'app') else None
                if orch is not None:
                    llm_manager = orch.llm
            except Exception:
                pass

        if llm_manager is None or not hasattr(llm_manager, 'generate'):
            return (
                log_content
                + "\n\n---\n"
                + "⚠️ No se pudo invocar el LLM de diagnóstico. "
                + "Arriba están los logs crudos para revisión manual."
            )

        # Filtrar ruido (Telegram polling, scheduler heartbeat) antes de enviar al LLM
        filtered_lines = [
            line for line in log_content.split("\n")
            if not _NOISE_RE.search(line)
            or re.search(r'\b(?:ERROR|CRITICAL|FATAL|Traceback|Exception)\b', line)
        ]
        if filtered_lines:
            log_content = "\n".join(filtered_lines)

        # Limitar contenido de logs para no exceder contexto
        max_chars = 4000
        if len(log_content) > max_chars:
            log_content = log_content[:max_chars] + "\n\n... (logs truncados, solo se muestran las primeras líneas)"

        prompt = _DIAGNOSIS_PROMPT.format(
            user_question=user_question,
            log_content=log_content,
        )

        system_instruction = (
            "Eres un diagnosticador experto de sistemas. "
            "Analiza logs y genera diagnósticos claros y accionables. "
            "Responde en español."
        )
        full_prompt = system_instruction + "\n\n" + prompt
        try:
            diagnosis = await asyncio.to_thread(
                lambda: llm_manager.generate(
                    full_prompt,
                    force_cloud=True,
                    force_local=False,
                )
            )
            return f"{log_content}\n\n---\n\n{diagnosis.strip()}"
        except Exception as e:
            return f"{log_content}\n\n⚠️ Error generando diagnóstico: {e}"

    # ── Internal ─────────────────────────────────────────────────────

    def _resolve(self, fname: str) -> Optional[Path]:
        """Whitelist + protección path traversal."""
        if fname not in _ALLOWED_NAMES:
            return None
        for d in _LOG_DIRS:
            p = (d / fname).resolve()
            try:
                d_resolved = d.resolve()
            except FileNotFoundError:
                continue
            # Garantizar que p está dentro de d (defensa contra symlinks)
            if str(p).startswith(str(d_resolved)) and p.exists():
                return p
        return None

    def _tail(self, fname: str, n: int) -> str:
        allowed = ", ".join(sorted(_ALLOWED_NAMES))
        if fname not in _ALLOWED_NAMES:
            return f"Archivo `{fname}` no está en la lista de logs permitidos. Disponibles: {allowed}."
        path = self._resolve(fname)
        if path is None:
            return f"Archivo `{fname}` no encontrado en disco. Disponibles: {allowed}."

        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                lines = deque(f, maxlen=n)
        except OSError as e:
            return f"Error leyendo {fname}: {e}"

        body = "".join(lines).rstrip()
        return (
            f"**Últimas {len(lines)} líneas de `{fname}`** "
            f"(`{path}`)\n\n```\n{body}\n```"
        )

    def _errors(self, window_seconds: int) -> str:
        cutoff = time.time() - window_seconds
        today = datetime.now().date()
        matches: list[tuple[str, str]] = []
        for fname in _ALLOWED_NAMES:
            path = self._resolve(fname)
            if path is None:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                mtime_date = datetime.fromtimestamp(path.stat().st_mtime).date()
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if not _ERROR_RE.search(line):
                            continue
                        ts = _parse_log_timestamp(line, mtime_date)
                        if ts is not None and ts < cutoff:
                            continue
                        matches.append((fname, line.rstrip()))
            except OSError:
                continue

        if not matches:
            return f"Sin errores en los últimos {window_seconds//60} minutos."

        # Solo las últimas 30 entradas (filtrando ruido de Telegram/scheduler)
        head = [m for m in matches[-50:] if not _NOISE_RE.search(m[1])][-30:]
        if not head:
            head = matches[-30:]  # fallback: mostrar todo si el filtro elimina todo
        body = "\n".join(f"[{f}] {l}" for f, l in head)
        return (
            f"**{len(matches)} errores recientes** (últimos {window_seconds//60} min):\n"
            f"\n```\n{body}\n```"
        )

    def _by_request_id(self, request_id: str) -> str:
        # Sanitizar request_id (alphanumeric + dashes + underscores)
        safe = "".join(c for c in request_id if c.isalnum() or c in "-_")[:64]
        if not safe:
            return "request_id inválido."

        matches: list[tuple[str, str]] = []
        for fname in _ALLOWED_NAMES:
            path = self._resolve(fname)
            if path is None:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if safe in line:
                            matches.append((fname, line.rstrip()))
            except OSError:
                continue

        if not matches:
            return f"Sin entradas para request_id `{safe}`."

        body = "\n".join(f"[{f}] {l}" for f, l in matches[-100:])
        return (
            f"**{len(matches)} líneas para request_id `{safe}`:**\n"
            f"\n```\n{body}\n```"
        )
