"""
DiagnosticAgent — Diagnóstico automático de errores del backend Zephyro.

Fases:
  1. Log Ingestion       — lee últimas N líneas, filtra ERROR/WARNING
  2. Code Context        — extrae ±20 líneas alrededor del error (read-only)
  3. Tier Router         — clasifica complejidad → herramienta (Copilot CLI / Haiku / Sonnet)
  4. Token Monitor       — estimación via tiktoken, avisos Telegram 65/85/95%
  5. Hypothesis Generator— prompt al LLM → JSON estricto
  6. Prompt Builder      — genera prompt Markdown desde template
  7. Approval Gate       — inline keyboard Telegram, obligatorio e inskipeable

Restricciones:
  - Completamente read-only sobre el codebase
  - Copilot CLI siempre es Tier 0 antes de gastar API
  - Approval gate es obligatorio
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

# ── Optional dependency: tiktoken ────────────────────────────────────────────
try:
    import tiktoken
    _HAS_TIKTOKEN = True
except ImportError:
    _HAS_TIKTOKEN = False


# ── Log format regex ─────────────────────────────────────────────────────────
# Soporta tanto el formato básico "HH:MM:SS [LEVEL] logger: msg"
# como el formato extendido con "... (filename.py:lineno)"
_LOG_RE = re.compile(
    r"^(?P<ts>\d{2}:\d{2}:\d{2})\s+\[(?P<level>ERROR|WARN(?:ING)?)\]\s+"
    r"(?P<logger>[\w.]+):\s+(?P<message>.+)$"
)
# Ruido de polling de canales. El bot de Telegram hace getUpdates cada 10s y cada
# poll deja una línea INFO de httpx: 36 de las últimas 50 líneas del log medidas
# el 2026-09-29. Con n_lines=50 la ventana del diagnóstico cubría ~8 minutos y
# nunca alcanzaba a ver el error que el usuario acababa de reportar.
#
# El filtro es A PROPÓSITO angosto: descartar cualquier línea no parseable
# rompería _enrich_with_tracebacks, que necesita las líneas del traceback (que
# justamente no matchean _LOG_RE).
_NOISE_RE = re.compile(
    r"httpx:\s*HTTP Request:.*api\.telegram\.org.*"
    r"(?:getUpdates|deleteWebhook|sendMessage|sendChatAction)",
    re.IGNORECASE,
)

# Traceback "File" lines
_FILE_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<lineno>\d+)')
# Inline location appended by extended log format  "(module.py:42)"
_INLINE_LOC_RE = re.compile(r"\((?P<file>[\w/\-.]+\.py):(?P<lineno>\d+)\)\s*$")


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    timestamp: str
    level: str           # "ERROR" | "WARNING"
    logger: str
    message: str
    filename: str        # vacío si no se puede extraer
    lineno: int          # 0 si no disponible
    stack_trace: str     # traceback completo si está presente
    raw: str
    dedup_key: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.dedup_key = f"{self.filename}:{self.lineno}:{self.message[:60]}"


@dataclass
class CodeContext:
    filepath: str        # ruta absoluta resuelta
    lineno: int          # línea objetivo (1-indexed)
    window_start: int    # línea de inicio de la ventana (1-indexed)
    window_end: int
    lines: list[str]     # líneas dentro de la ventana
    target_line: str     # contenido de la línea objetivo


@dataclass
class DiagnosisResult:
    causa: str
    archivos_afectados: list[str]
    solucion_propuesta: str
    confianza: int       # 0-100
    tier_usado: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Divide texto respetando saltos de línea (igual que TelegramChannel)."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ── Main class ────────────────────────────────────────────────────────────────

class DiagnosticAgent:
    """Agente de diagnóstico multi-tier con aprobación obligatoria via Telegram."""

    # Límites de tokens por tier
    MODEL_LIMITS: dict[int, int] = {
        1: 200_000,   # Haiku
        2: 200_000,   # Sonnet
        3: 32_000,    # Sonnet extended thinking (budget seguro)
    }

    # Umbral para considerar "muchos módulos" al rutear tiers
    _MULTI_MODULE_THRESHOLD = 3

    def __init__(
        self,
        llm_manager: Any,
        log_file: str,
        code_root: str,
        gate: Any = None,
        telegram_channel: Any = None,
        code_context: str = "",
        user_context: str = "",
    ) -> None:
        self._llm = llm_manager
        self._code_context = code_context
        self._user_context = user_context
        if gate is None and telegram_channel is not None:
            from channels.telegram_channel import TelegramApprovalGate
            gate = TelegramApprovalGate(telegram_channel)
        if gate is None:
            raise ValueError("DiagnosticAgent requires either `gate` or `telegram_channel`")
        self._gate = gate
        self._log_file = Path(log_file)
        self._code_root = Path(code_root)

        # Logger propio al archivo de diagnóstico
        diag_log = self._code_root.parent / "logs" / "diagnostic_agent.log"
        diag_log.parent.mkdir(parents=True, exist_ok=True)
        self._log = logging.getLogger("zephyro.diagnostic")
        if not self._log.handlers:
            fh = logging.FileHandler(diag_log)
            fh.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            self._log.addHandler(fh)
            self._log.setLevel(logging.DEBUG)

        # Ruta del template de fix
        self._template_path = (
            Path(__file__).parent.parent / "prompts" / "claude_code_fix_template.md"
        )

    # ── Phase 1: Log Ingestion ────────────────────────────────────────────────

    def _tail_log_file(self, n: int) -> list[str]:
        """Lee las últimas N líneas del archivo de log sin cargar todo en memoria."""
        if not self._log_file.exists():
            self._log.warning(
                "Archivo de log no encontrado: %s. "
                "Asegúrate de que ZEPHYRO_LOG_FILE apunta a un archivo existente.",
                self._log_file,
            )
            return []
        try:
            with open(self._log_file, "rb") as fh:
                # Estrategia: leer en chunks desde el final
                chunk_size = 8192
                fh.seek(0, 2)
                file_size = fh.tell()
                pos = file_size
                lines: list[str] = []
                remainder = b""
                while pos > 0 and len(lines) < n:
                    read_size = min(chunk_size, pos)
                    pos -= read_size
                    fh.seek(pos)
                    chunk = fh.read(read_size) + remainder
                    raw_lines = chunk.split(b"\n")
                    remainder = raw_lines[0]
                    for raw in reversed(raw_lines[1:]):
                        decoded = raw.decode("utf-8", errors="replace")
                        # El ruido de polling no cuenta para `n`: así "50 líneas"
                        # significan 50 líneas útiles y la ventana cubre horas en
                        # vez de minutos.
                        if _NOISE_RE.search(decoded):
                            continue
                        lines.append(decoded)
                        if len(lines) >= n:
                            break
                if remainder and len(lines) < n:
                    decoded = remainder.decode("utf-8", errors="replace")
                    if not _NOISE_RE.search(decoded):
                        lines.append(decoded)
            return list(reversed(lines[-n:]))
        except OSError as exc:
            self._log.error("Error leyendo log: %s", exc)
            return []

    def _parse_log_line(self, line: str) -> Optional[LogEntry]:
        """Parsea una línea de log al formato estándar de Zephyro."""
        m = _LOG_RE.match(line.strip())
        if not m:
            return None
        filename = ""
        lineno = 0
        # Intentar extraer ubicación inline  (file.py:42)
        loc = _INLINE_LOC_RE.search(m.group("message"))
        if loc:
            filename = loc.group("file")
            lineno = int(loc.group("lineno"))
        raw_level = m.group("level")
        level = "WARNING" if raw_level.startswith("WARN") else raw_level
        return LogEntry(
            timestamp=m.group("ts"),
            level=level,
            logger=m.group("logger"),
            message=m.group("message"),
            filename=filename,
            lineno=lineno,
            stack_trace="",
            raw=line,
        )

    def _enrich_with_tracebacks(self, entries: list[LogEntry], raw_lines: list[str]) -> None:
        """Post-proceso: asocia tracebacks multi-línea a su LogEntry."""
        # Construir índice por línea raw
        entry_by_raw = {e.raw.strip(): e for e in entries}
        current_entry: Optional[LogEntry] = None
        tb_lines: list[str] = []

        for line in raw_lines:
            if _LOG_RE.match(line.strip()):
                # Guardar traceback acumulado en el entry anterior
                if current_entry is not None and tb_lines:
                    current_entry.stack_trace = "\n".join(tb_lines)
                    # Intentar extraer filename:lineno del traceback
                    if not current_entry.filename:
                        for tb_line in tb_lines:
                            fm = _FILE_RE.search(tb_line)
                            if fm:
                                current_entry.filename = Path(fm.group("path")).name
                                current_entry.lineno = int(fm.group("lineno"))
                                current_entry.dedup_key = (
                                    f"{current_entry.filename}:{current_entry.lineno}"
                                    f":{current_entry.message[:60]}"
                                )
                                break
                current_entry = entry_by_raw.get(line.strip())
                tb_lines = []
            elif current_entry is not None:
                tb_lines.append(line.rstrip())

        if current_entry is not None and tb_lines:
            current_entry.stack_trace = "\n".join(tb_lines)

    def _filter_errors(self, entries: list[LogEntry]) -> list[LogEntry]:
        return [e for e in entries if e.level in ("ERROR", "WARNING")]

    def _deduplicate(self, entries: list[LogEntry]) -> list[LogEntry]:
        """Elimina duplicados manteniendo la primera ocurrencia."""
        seen: dict[str, LogEntry] = {}
        for e in entries:
            if e.dedup_key not in seen:
                seen[e.dedup_key] = e
        return list(seen.values())

    # ── Phase 2: Code Context Resolver ───────────────────────────────────────

    def _resolve_code_context(self, entry: LogEntry) -> Optional[CodeContext]:
        """Extrae ±20 líneas alrededor del error. Read-only."""
        if not entry.filename or not entry.lineno:
            return None

        # Buscar el archivo en code_root
        candidates = list(self._code_root.rglob(Path(entry.filename).name))
        # Filtrar .venv
        candidates = [c for c in candidates if ".venv" not in c.parts]
        if not candidates:
            return None
        filepath = candidates[0]

        try:
            all_lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

        total = len(all_lines)
        window = 20
        start = max(0, entry.lineno - window - 1)
        end = min(total, entry.lineno + window)
        window_lines = all_lines[start:end]
        target = all_lines[entry.lineno - 1] if 0 < entry.lineno <= total else ""

        return CodeContext(
            filepath=str(filepath),
            lineno=entry.lineno,
            window_start=start + 1,
            window_end=end,
            lines=window_lines,
            target_line=target,
        )

    # ── Phase 3: Tier Router ─────────────────────────────────────────────────

    def _classify_tier(self, entries: list[LogEntry]) -> int:
        """
        Tier 0: gh disponible Y 1 error Y no ImportError/AttributeError
        Tier 1: ≤5 errores O solo WARNINGs O único módulo
        Tier 2: stack traces Y múltiples archivos
        Tier 3: errores recursivos / imports circulares / async nested
        """
        has_traceback = any(e.stack_trace for e in entries)
        unique_files = {e.filename for e in entries if e.filename}
        error_count = sum(1 for e in entries if e.level == "ERROR")
        warning_only = all(e.level == "WARNING" for e in entries)

        critical_patterns = re.compile(
            r"(RecursionError|circular import|RuntimeError.*coroutine|"
            r"Event loop.*closed|Task.*exception was never retrieved)",
            re.IGNORECASE,
        )
        has_critical = any(critical_patterns.search(e.message) for e in entries)

        if has_critical:
            return 3

        if has_traceback and len(unique_files) >= self._MULTI_MODULE_THRESHOLD:
            return 2

        if error_count <= 1 and not has_traceback:
            # Sólo tier 0 si gh está disponible
            if self._gh_available():
                return 0
            return 1

        if warning_only or error_count <= 5:
            return 1

        return 2

    @staticmethod
    def _gh_available() -> bool:
        try:
            subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                timeout=3,
                check=False,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def peek_tier(self, n_lines: int = 50) -> Optional[int]:
        """Quick pre-classify of the tier (without spawning the full agent).

        Reads recent log lines, deduplicates errors, and runs _classify_tier()
        only. Returns the tier int, or None if no errors found. Used by the
        orchestrator to surface the tier in the user-facing response before
        the full run() finishes.
        """
        try:
            raw_lines = self._tail_log_file(n_lines)
            if not raw_lines:
                return None
            entries: list[LogEntry] = []
            for line in raw_lines:
                e = self._parse_log_line(line)
                if e:
                    entries.append(e)
            self._enrich_with_tracebacks(entries, raw_lines)
            filtered = self._filter_errors(entries)
            deduped = self._deduplicate(filtered)
            if not deduped:
                return None
            return self._classify_tier(deduped)
        except Exception as exc:
            self._log.debug("peek_tier failed: %s", exc)
            return None

    # ── Tier 0: Copilot CLI + OpenCode fallback ──────────────────────────────

    # Tier → OpenCode model. Tier 0 uses the free model; higher tiers can use
    # the paid DeepSeek variants. Tier 3 stays on Claude (handled outside).
    _OPENCODE_MODELS: dict[int, str] = {
        0: "opencode/deepseek-v4-flash-free",
        1: "deepseek/deepseek-v4-flash",
        2: "deepseek/deepseek-v4-pro",
    }

    @staticmethod
    def _opencode_available() -> bool:
        try:
            subprocess.run(
                ["opencode", "--version"],
                capture_output=True, timeout=3, check=False,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _build_tier0_prompt(
        self, entry: LogEntry, ctx: Optional[CodeContext]
    ) -> str:
        """Construye el prompt compartido para Copilot CLI y OpenCode."""
        snippet = ""
        if ctx:
            lines_with_marker = []
            for i, line in enumerate(ctx.lines):
                lineno = ctx.window_start + i
                marker = ">>> " if lineno == ctx.lineno else "    "
                lines_with_marker.append(f"{marker}{lineno:4d}: {line}")
            snippet = "\n".join(lines_with_marker)

        prompt = (
            f"Error en {entry.filename}:{entry.lineno}\n"
            f"Mensaje: {entry.message}\n"
        )
        if entry.stack_trace:
            prompt += f"\nTraceback:\n{entry.stack_trace[:800]}\n"
        if snippet:
            prompt += f"\nCódigo:\n{snippet}\n"
        prompt += "\n¿Cuál es la causa y cómo se soluciona?"
        return prompt

    async def _call_tier0_copilot(
        self, entry: LogEntry, ctx: Optional[CodeContext]
    ) -> tuple[bool, str]:
        """Invoca `gh copilot suggest --agent shell`. Retorna (éxito, output)."""
        prompt = self._build_tier0_prompt(entry, ctx)
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "copilot", "suggest", "--agent", "shell", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
            if proc.returncode == 0 and stdout.strip():
                self._log.info("Tier 0 (Copilot CLI) respondió correctamente")
                return True, stdout.decode(errors="replace").strip()
            return False, ""
        except (FileNotFoundError, asyncio.TimeoutError) as exc:
            self._log.info("Copilot CLI no disponible (%s)", exc)
            return False, ""

    async def _call_opencode(
        self, prompt: str, tier: int,
        gate=None, chat_id=None,
    ) -> tuple[bool, str]:
        """Invoca `opencode run -m <model> "<prompt>"`. Retorna (éxito, output).

        When gate and chat_id are provided, each stdout line is streamed as a
        diag:progress message so the user sees real-time output in the modal.
        """
        model = self._OPENCODE_MODELS.get(tier, self._OPENCODE_MODELS[0])

        async def _collect_lines(stream) -> list[str]:
            collected: list[str] = []
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                collected.append(line)
                if gate and chat_id:
                    await gate.send_message(chat_id, f"[OpenCode] {line}")
            return collected

        try:
            proc = await asyncio.create_subprocess_exec(
                "opencode", "run", "-m", model, prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                lines = await asyncio.wait_for(_collect_lines(proc.stdout), timeout=60)
                await proc.wait()
            except asyncio.TimeoutError:
                proc.kill()
                self._log.info("OpenCode timeout tras 60s")
                return False, ""
            if proc.returncode == 0 and lines:
                self._log.info("OpenCode (%s) respondió correctamente", model)
                return True, "\n".join(lines)
            return False, ""
        except FileNotFoundError as exc:
            self._log.info("OpenCode no disponible (%s)", exc)
            return False, ""

    # ── Phase 4: Token Monitor ────────────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        """Estima tokens usando tiktoken (cl100k_base). Fallback: chars / 4."""
        if _HAS_TIKTOKEN:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        return max(1, len(text) // 4)

    async def _check_token_budget(
        self, prompt: str, tier: int, chat_id: int
    ) -> float:
        """
        Verifica presupuesto de tokens. Envía avisos Telegram en 65/85/95%.
        Retorna ratio (0.0-1.0+). Si ratio >= 0.95, el caller debe abortar.
        """
        count = self._estimate_tokens(prompt)
        limit = self.MODEL_LIMITS.get(tier, 200_000)
        ratio = count / limit

        self._log.debug("Tokens estimados: %d / %d (%.0f%%)", count, limit, ratio * 100)

        if ratio >= 0.95:
            msg = (
                f"🔴 Contexto al {ratio*100:.0f}% ({count}/{limit} tokens) — "
                "recomiendo /compact antes de continuar"
            )
        elif ratio >= 0.85:
            msg = (
                f"🔶 Contexto al {ratio*100:.0f}% ({count}/{limit} tokens) — "
                "considera /compact pronto"
            )
        elif ratio >= 0.65:
            msg = (
                f"⚠️ Contexto al {ratio*100:.0f}% ({count}/{limit} tokens) — "
                "sin urgencia aún"
            )
        else:
            return ratio

        await self._send_simple(chat_id, msg)
        return ratio

    async def _send_simple(self, chat_id: Any, text: str) -> None:
        """Envía un mensaje de progreso simple por el gate configurado."""
        try:
            await self._gate.send_message(chat_id, text)
        except Exception as exc:
            self._log.warning("Error enviando mensaje via gate: %s", exc)

    # ── Phase 5: Hypothesis Generator ────────────────────────────────────────

    def _build_hypothesis_prompt(
        self, entries: list[LogEntry], contexts: list[CodeContext]
    ) -> str:
        lines = [
            "Eres un debugger experto. Analiza el siguiente error y código.",
            "Responde SOLO con JSON válido, sin preamble, sin markdown, sin texto adicional.",
            "",
            "Esquema requerido:",
            '{"causa": "string", "archivos_afectados": ["list"], '
            '"solucion_propuesta": "string", "confianza": 0-100, "tier_usado": 0-3}',
            "",
        ]

        if self._user_context:
            lines += [
                "=== SOLICITUD DEL USUARIO (contexto que desencadenó el diagnóstico) ===",
                self._user_context[:600],
                "",
            ]

        lines.append("=== ERRORES DETECTADOS ===")

        for i, entry in enumerate(entries[:10], 1):
            lines.append(
                f"\n[{i}] {entry.timestamp} [{entry.level}] {entry.logger}: {entry.message}"
            )
            if entry.stack_trace:
                lines.append(f"Traceback:\n{entry.stack_trace[:600]}")

        if contexts:
            lines.append("\n=== CÓDIGO CONTEXTO ===")
            for ctx in contexts[:3]:
                rel_path = Path(ctx.filepath).relative_to(self._code_root) \
                    if self._code_root in Path(ctx.filepath).parents else Path(ctx.filepath).name
                lines.append(f"\n### {rel_path}:{ctx.lineno}")
                for i, line in enumerate(ctx.lines):
                    lineno = ctx.window_start + i
                    marker = ">>>" if lineno == ctx.lineno else "   "
                    lines.append(f"{marker} {lineno:4d}: {line}")

        if self._code_context:
            lines.append("\n=== ESTRUCTURA DEL CÓDIGO (GitNexus) ===")
            lines.append(self._code_context[:1500])

        verif_entries = self._load_recent_verif_failures()
        if verif_entries:
            lines.append("\n=== VERIFICACIONES RECIENTES (FAIL / inconcluso) ===")
            for ve in verif_entries:
                lines.append(ve)

        return "\n".join(lines)

    def _load_recent_verif_failures(self, n_lines: int = 40) -> list[str]:
        """Lee las últimas n líneas del audit de verificación y retorna entradas FAIL/[?] recientes."""
        import time as _time
        audit_path = self._code_root.parent / "logs" / "verification_audit.jsonl"
        if not audit_path.exists():
            return []
        try:
            raw = audit_path.read_text(encoding="utf-8", errors="replace").splitlines()
            recent_raw = raw[-n_lines:]
            cutoff = _time.time() - 86400  # últimas 24h
            results: list[str] = []
            for line in reversed(recent_raw):
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("ts", 0) < cutoff:
                    continue
                verdict = entry.get("verdict", "")
                if "FAIL" not in verdict and "[?]" not in verdict:
                    continue
                from datetime import datetime
                ts_str = datetime.fromtimestamp(entry["ts"]).strftime("%H:%M:%S")
                results.append(
                    f"[{ts_str}] skill={entry.get('skill','?')} "
                    f"input={repr(entry.get('input','')[:60])} "
                    f"verdict={repr(verdict[:80])}"
                )
                if len(results) >= 5:
                    break
            return results
        except Exception:
            return []

    async def _generate_hypothesis(
        self, prompt: str, tier: int
    ) -> DiagnosisResult:
        """Llama al LLM del tier correspondiente y parsea JSON estricto."""
        self._log.info("Generando hipótesis con Tier %d", tier)

        if tier == 1:
            result = await self._generate_hypothesis_t1(prompt)
        elif tier == 2:
            result = await self._generate_hypothesis_t2(prompt)
        else:
            result = await self._generate_hypothesis_t3_with_choice(prompt)

        self._log.info(
            "Hipótesis generada: confianza=%d%%, tier=%d, archivos=%s",
            result.confianza, result.tier_usado, result.archivos_afectados,
        )
        return result

    async def _generate_hypothesis_t1(self, prompt: str) -> DiagnosisResult:
        """Tier 1: DeepSeek v4 flash directo (rápido + barato)."""
        from core.llm_helpers import call_deepseek_flash
        raw = await call_deepseek_flash(prompt)
        if not raw:
            self._log.warning("DeepSeek flash no disponible, fallback a llm_manager T1")
            raw = await asyncio.to_thread(self._llm.generate, prompt, force_cloud=True)
        return self._parse_hypothesis_json(raw or "", tier=1)

    async def _generate_hypothesis_t2(self, prompt: str) -> DiagnosisResult:
        """Tier 2: Claude Sonnet 4.6 directo."""
        from core.llm_helpers import call_sonnet
        raw = await call_sonnet(prompt)
        if not raw:
            self._log.warning("Sonnet no disponible, fallback a llm_manager T2")
            raw = await asyncio.to_thread(self._llm.generate, prompt, force_cloud=True)
        return self._parse_hypothesis_json(raw or "", tier=2)

    async def _generate_hypothesis_t3_with_choice(self, prompt: str) -> DiagnosisResult:
        """Tier 3: notifica al usuario para elegir modelo (DeepSeek pro vs Opus+thinking)."""
        from core.llm_helpers import call_deepseek_pro, call_opus_thinking

        # Clasificar tipo de problema
        is_context_heavy = len(prompt) > 5000 or prompt.count("File ") > 3
        problem_type = "contexto extenso" if is_context_heavy else "razonamiento complejo"
        recommended_id = "deepseek_pro" if is_context_heavy else "opus"

        options = [
            {"id": "deepseek_pro", "label": "DeepSeek v4 Pro",
             "description": "Mejor para contexto extenso (múltiples archivos, logs largos)"},
            {"id": "opus", "label": "Claude Opus 4.7 + thinking",
             "description": "Mejor para razonamiento complejo (lógica, concurrencia)"},
        ]

        user_choice = recommended_id
        if self._gate and hasattr(self._gate, "request_model_choice"):
            try:
                # ws_user_id: para WS gate usamos el chat_id como string (el gate lo mapea al WS user)
                # Para el gate Telegram, request_model_choice no existe (fallback al default)
                ws_uid = str(getattr(self, "_current_chat_id", "admin") or "admin")
                user_choice = await self._gate.request_model_choice(
                    user_id=ws_uid,
                    problem_type=problem_type,
                    options=options,
                    timeout=120.0,
                )
            except Exception as exc:
                self._log.warning("model_choice gate error: %s — usando recomendado", exc)

        self._log.info("T3 model choice: %s (problem_type=%s)", user_choice, problem_type)

        if user_choice == "deepseek_pro":
            raw = await call_deepseek_pro(prompt)
            if raw:
                return self._parse_hypothesis_json(raw, tier=3)

        # opus (o fallback si deepseek_pro falla)
        raw = await call_opus_thinking(prompt)
        if raw:
            return self._parse_hypothesis_json(raw, tier=3)

        # Último recurso
        self._log.warning("Todos los modelos T3 fallaron, usando llm_manager")
        raw = await asyncio.to_thread(self._llm.generate, prompt, force_cloud=True)
        return self._parse_hypothesis_json(raw or "", tier=2)

    @staticmethod
    def _parse_hypothesis_json(raw: str, tier: int) -> DiagnosisResult:
        """Extrae JSON del texto de respuesta del LLM."""
        # Intentar parseo directo
        cleaned = raw.strip()
        # Eliminar posible markdown ```json ... ```
        md = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", cleaned)
        if md:
            cleaned = md.group(1)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Buscar el primer objeto JSON en el texto
            m = re.search(r"\{[\s\S]+\}", cleaned)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}

        return DiagnosisResult(
            causa=str(data.get("causa", "No determinada")),
            archivos_afectados=list(data.get("archivos_afectados", [])),
            solucion_propuesta=str(data.get("solucion_propuesta", "Ver log completo")),
            confianza=int(data.get("confianza", 50)),
            tier_usado=int(data.get("tier_usado", tier)),
        )

    # ── Phase 6: Prompt Builder ───────────────────────────────────────────────

    def _build_claude_code_prompt(
        self,
        entries: list[LogEntry],
        contexts: list[CodeContext],
        hypothesis: DiagnosisResult,
    ) -> str:
        """Rellena el template Markdown con los datos del diagnóstico."""
        if not self._template_path.exists():
            template = _FALLBACK_TEMPLATE
        else:
            template = self._template_path.read_text(encoding="utf-8")

        # Error log text
        error_log_lines = []
        for e in entries[:10]:
            error_log_lines.append(
                f"[{e.timestamp}] [{e.level}] {e.logger}: {e.message}"
            )
            if e.stack_trace:
                error_log_lines.append(e.stack_trace[:400])
        error_log_text = "\n".join(error_log_lines)

        # Code context blocks
        ctx_blocks = []
        for ctx in contexts[:3]:
            rel = (
                Path(ctx.filepath).relative_to(self._code_root)
                if self._code_root in Path(ctx.filepath).parents
                else Path(ctx.filepath).name
            )
            block_lines = [f"### {rel}:{ctx.lineno}", "```python"]
            for i, line in enumerate(ctx.lines):
                lineno = ctx.window_start + i
                marker = ">>>" if lineno == ctx.lineno else "   "
                block_lines.append(f"{marker} {lineno:4d}: {line}")
            block_lines.append("```")
            ctx_blocks.append("\n".join(block_lines))
        code_context_blocks = "\n\n".join(ctx_blocks) if ctx_blocks else "_No se pudo resolver el contexto de código._"

        archivos_str = ", ".join(hypothesis.archivos_afectados) or "desconocido"

        return template.format(
            tier_usado=hypothesis.tier_usado,
            confianza=hypothesis.confianza,
            archivos_afectados=archivos_str,
            causa=hypothesis.causa,
            solucion_propuesta=hypothesis.solucion_propuesta,
            error_log_text=error_log_text,
            code_context_blocks=code_context_blocks,
        )

    # ── Phase 7: Approval Gate ────────────────────────────────────────────────

    async def _request_approval(
        self, chat_id: Any, hypothesis: DiagnosisResult, fix_prompt: str
    ) -> str:
        """
        Envía solicitud de aprobación por el gate configurado.
        Retorna: "view" | "ignore" | "more_context"
        """
        archivos = ", ".join(hypothesis.archivos_afectados) or "módulo desconocido"
        summary = (
            f"🔍 Detecté un error en *{archivos}* "
            f"(confianza: {hypothesis.confianza}%).\n\n"
            f"*Causa:* {hypothesis.causa[:200]}\n\n"
            "¿Quieres ver el diagnóstico completo y prompt de solución?"
        )
        try:
            request_id = await self._gate.send_approval_request(chat_id, summary)
            action = await self._gate.wait_for_approval(request_id, timeout=300.0)
            return action
        except Exception as exc:
            self._log.error("Error en approval gate: %s", exc)
            return "ignore"

    # ── Orchestration ─────────────────────────────────────────────────────────

    async def run(self, chat_id: Any, n_lines: int = 500) -> str:
        """Punto de entrada principal.

        Envuelve a `_run_phases` para garantizar que el panel del frontend SIEMPRE
        reciba un evento terminal. Antes, los early returns de `_run_phases`
        ("no hay líneas de log", "no hay errores") devolvían un string sin emitir
        nada, y `src/diagnostic.js` —que cierra con `diag:resolved`/`diag:expired`—
        quedaba en RUNNING para siempre. El chat sí cerraba, porque va por otro
        canal (`diagnostic_running/done`), lo que hacía el bug más difícil de ver.
        """
        summary = ""
        try:
            summary = await self._run_phases(chat_id, n_lines)
            return summary
        except Exception as exc:
            summary = f"El diagnóstico falló: {exc}"
            raise
        finally:
            # send_resolved es no-op si el run ya tiene estado terminal (el camino
            # de aprobación lo resuelve routers/chat.py). Defensivo con getattr:
            # los otros adapters del protocolo de gate no lo implementan.
            _send_resolved = getattr(self._gate, "send_resolved", None)
            if _send_resolved is not None:
                try:
                    await _send_resolved(chat_id, summary=summary)
                except Exception as exc:
                    self._log.warning("No pude emitir el evento terminal: %s", exc)

    async def _run_phases(self, chat_id: Any, n_lines: int = 500) -> str:
        """Ejecuta todas las fases en secuencia."""
        self._log.info("DiagnosticAgent.run() iniciado (n_lines=%d, chat_id=%s)", n_lines, chat_id)
        self._current_chat_id = chat_id  # Disponible para T3 model-choice

        # Emitir contexto del usuario como primer evento de progreso
        if self._user_context:
            await self._send_simple(chat_id, f"Contexto: {self._user_context[:200]}")

        # ── Fase 1: Ingesta de logs ──────────────────────────────────────────
        raw_lines = self._tail_log_file(n_lines)
        if not raw_lines:
            return (
                "📭 No se encontraron líneas de log.\n"
                f"Verifica que `{self._log_file}` existe y tiene contenido.\n"
                "Configura `ZEPHYRO_LOG_FILE` en `.env` si es necesario."
            )

        entries: list[LogEntry] = []
        for line in raw_lines:
            e = self._parse_log_line(line)
            if e:
                entries.append(e)

        self._enrich_with_tracebacks(entries, raw_lines)
        filtered = self._filter_errors(entries)
        deduped = self._deduplicate(filtered)

        self._log.info(
            "Log ingested: %d lines → %d entries → %d filtered → %d deduped",
            len(raw_lines), len(entries), len(filtered), len(deduped),
        )

        if not deduped:
            return "✅ No se encontraron errores ni advertencias en los últimos logs."

        await self._send_simple(
            chat_id,
            f"📋 Encontré {len(deduped)} error(es)/advertencia(s) únicos. Analizando...",
        )

        # ── Fase 2: Code context ─────────────────────────────────────────────
        contexts = [c for e in deduped if (c := self._resolve_code_context(e))]
        self._log.info("Code contexts resueltos: %d / %d", len(contexts), len(deduped))

        # ── Fase 3: Tier router ──────────────────────────────────────────────
        tier = self._classify_tier(deduped)
        self._log.info("Tier seleccionado: %d", tier)

        # ── Tier 0: Copilot CLI → OpenCode fallback ─────────────────────────
        if tier == 0:
            # 0.a) Probe Copilot CLI (requires `gh auth login` + valid Copilot sub)
            ok, tier0_result = await self._call_tier0_copilot(
                deduped[0], contexts[0] if contexts else None
            )
            source_label = "Copilot CLI"
            # 0.b) If Copilot failed, fall back to OpenCode + deepseek-v4-flash-free
            if not ok and self._opencode_available():
                prompt = self._build_tier0_prompt(
                    deduped[0], contexts[0] if contexts else None
                )
                ok, tier0_result = await self._call_opencode(
                    prompt, tier=0, gate=self._gate, chat_id=chat_id,
                )
                source_label = f"OpenCode ({self._OPENCODE_MODELS[0]})"

            if ok:
                # Tier 0 tuvo éxito — aún necesitamos aprobación
                await self._send_simple(chat_id, f"🤖 {source_label} respondió. Preparando aprobación...")
                hypothesis = DiagnosisResult(
                    causa=f"Análisis {source_label}: {tier0_result[:300]}",
                    archivos_afectados=[deduped[0].filename] if deduped[0].filename else [],
                    solucion_propuesta=tier0_result,
                    confianza=70,
                    tier_usado=0,
                )
                fix_prompt = self._build_claude_code_prompt(deduped, contexts, hypothesis)
                action = await self._request_approval(chat_id, hypothesis, fix_prompt)
                return await self._handle_approval_action(
                    action, chat_id, deduped, contexts, hypothesis, fix_prompt,
                    hyp_prompt="", tier=tier
                )
            else:
                tier = 1
                await self._send_simple(chat_id, "⚠️ Tier 0 no disponible (Copilot ni OpenCode), escalando a Tier 1 (DeepSeek flash)...")

        # ── Fases 4-5: Token check + hipótesis ──────────────────────────────
        hyp_prompt = self._build_hypothesis_prompt(deduped, contexts)
        ratio = await self._check_token_budget(hyp_prompt, tier, chat_id)

        if ratio >= 0.95:
            return (
                "🔴 Abortado: presupuesto de tokens al 95%.\n"
                "Reduce `n_lines` o usa /compact antes de continuar."
            )

        await self._send_simple(chat_id, f"🧠 Consultando LLM (Tier {tier})...")
        hypothesis = await self._generate_hypothesis(hyp_prompt, tier)

        # ── Fase 6: Prompt builder ───────────────────────────────────────────
        fix_prompt = self._build_claude_code_prompt(deduped, contexts, hypothesis)

        # ── Fase 7: Approval gate (obligatorio) ─────────────────────────────
        action = await self._request_approval(chat_id, hypothesis, fix_prompt)

        return await self._handle_approval_action(
            action, chat_id, deduped, contexts, hypothesis, fix_prompt,
            hyp_prompt=hyp_prompt, tier=tier,
        )

    async def _handle_approval_action(
        self,
        action: str,
        chat_id: int,
        deduped: list[LogEntry],
        contexts: list[CodeContext],
        hypothesis: DiagnosisResult,
        fix_prompt: str,
        hyp_prompt: str,
        tier: int,
    ) -> str:
        if action == "ignore":
            return "Diagnóstico ignorado."

        if action == "more_context":
            new_tier = min(tier + 1, 3)
            self._log.info("Usuario pidió más contexto — escalando a Tier %d", new_tier)
            await self._send_simple(chat_id, f"🔍 Re-analizando con Tier {new_tier}...")

            if not hyp_prompt:
                hyp_prompt = self._build_hypothesis_prompt(deduped, contexts)

            hypothesis = await self._generate_hypothesis(hyp_prompt, new_tier)
            fix_prompt = self._build_claude_code_prompt(deduped, contexts, hypothesis)
            action = await self._request_approval(chat_id, hypothesis, fix_prompt)

            if action == "ignore":
                return "Diagnóstico ignorado tras escalado."
            if action == "more_context":
                # Máximo 1 escalado extra
                await self._send_simple(
                    chat_id, "⚠️ Ya estás en el tier máximo disponible."
                )

        # Acción "view": enviar prompt completo
        for chunk in _split_message(fix_prompt):
            await self._gate.send_message(chat_id, chunk)

        # Sin emojis y con la causa incluida: este string se muestra en el
        # chat y se sintetiza por TTS en call mode.
        _causa = (hypothesis.causa or "").strip()
        _causa_part = f" Causa: {_causa[:200]}" if _causa else ""
        return (
            f"Diagnóstico completado "
            f"(Tier {hypothesis.tier_usado}, confianza {hypothesis.confianza}%)."
            f"{_causa_part}"
        )


def _extract_spoken_summary(summary: str) -> str:
    """
    Versión corta y apta para TTS del resumen final del diagnóstico.

    Prioriza la causa raíz sobre los metadatos (tier/confianza), que en voz
    son ruido. Para resúmenes sin causa (ignorado/fallido) devuelve el texto
    tal cual.
    """
    if not summary:
        return ""
    if "Causa:" in summary:
        causa = summary.split("Causa:", 1)[1].strip()
        if causa:
            return f"Diagnóstico completado. La causa: {causa}"
    return summary


# ── Fallback template (si no existe el archivo) ───────────────────────────────

_FALLBACK_TEMPLATE = """\
# Zephyro Diagnostic Fix Request

**Tier:** {tier_usado} | **Confianza:** {confianza}% | **Archivos:** {archivos_afectados}

## Causa raíz identificada
{causa}

## Solución propuesta
{solucion_propuesta}

---

## Error log
```
{error_log_text}
```

## Code context
{code_context_blocks}

---

## Instrucciones para Claude Code

Genera el fix mínimo necesario.

**Restricciones:**
- Solo modifica `{archivos_afectados}`
- Sin nuevas dependencias externas sin justificación
- Compatible con Python 3.11+ y asyncio
- Mantén estilo: snake_case, type hints, docstrings en español

**Formato de respuesta:**
1. Confirmación de causa raíz
2. Diff mínimo (`--- a/file`, `+++ b/file`)
3. Tests afectados (si existen)
4. Riesgos del fix
"""
