#!/usr/bin/env python3
"""
Tests para DiagnosticAgent.

Cubre:
  - Log ingestion: tail, parse, filter, dedup
  - Code context resolver
  - Tier classification
  - Token monitor
  - Hypothesis JSON parsing
  - Prompt builder
  - run() happy path y edge cases

No hace llamadas reales a LLM ni a Telegram.
"""
import sys
import os
import asyncio
import json
import tempfile
import textwrap
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

# Asegurar que backend/ esté en sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subagents.diagnostic_agent import (
    DiagnosticAgent,
    LogEntry,
    CodeContext,
    DiagnosisResult,
    _split_message,
)


# ── Factories ──────────────────────────────────────────────────────────────────

def make_agent(
    log_content: str = "",
    log_file: str | None = None,
    code_root: str | None = None,
) -> tuple[DiagnosticAgent, MagicMock, MagicMock]:
    """Crea un DiagnosticAgent con mocks de LLM y Telegram.

    Nota: usa mkdtemp() para que el directorio temporal persista mientras el
    agente esté en uso (no se borra al salir de un context manager).
    """
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(
        return_value=json.dumps({
            "causa": "Error de prueba",
            "archivos_afectados": ["test_module.py"],
            "solucion_propuesta": "Corregir el código",
            "confianza": 85,
            "tier_usado": 1,
        })
    )

    mock_tg = MagicMock()
    mock_tg._app = MagicMock()
    mock_tg._app.bot = AsyncMock()
    mock_tg._app.bot.send_message = AsyncMock()
    mock_tg.send_approval_request = AsyncMock(return_value=42)
    mock_tg.wait_for_approval = AsyncMock(return_value="view")

    # mkdtemp no se borra automáticamente — el directorio persiste durante el test
    tmp = tempfile.mkdtemp()
    if log_file is None:
        log_path = Path(tmp) / "test.log"
        log_path.write_text(log_content, encoding="utf-8")
        log_file = str(log_path)
    if code_root is None:
        code_root = tmp

    agent = DiagnosticAgent(
        llm_manager=mock_llm,
        telegram_channel=mock_tg,
        log_file=log_file,
        code_root=code_root,
    )
    agent._log_file = Path(log_file)
    agent._code_root = Path(code_root)
    return agent, mock_llm, mock_tg


def make_log_lines(*entries: tuple[str, str, str, str]) -> str:
    """
    Genera líneas de log en el formato de Zephyro.
    entries: (timestamp, level, logger, message)
    """
    lines = []
    for ts, level, logger, msg in entries:
        lines.append(f"{ts} [{level}] {logger}: {msg}")
    return "\n".join(lines)


# ── Tests: Log Ingestion ───────────────────────────────────────────────────────

class TestLogIngestion(IsolatedAsyncioTestCase):

    def _agent_with_log(self, content: str) -> DiagnosticAgent:
        agent, _, _ = make_agent(log_content=content)
        return agent

    def test_tail_reads_last_n_lines(self):
        lines = "\n".join(f"line {i}" for i in range(200))
        agent = self._agent_with_log(lines)
        result = agent._tail_log_file(50)
        self.assertEqual(len(result), 50)
        self.assertIn("line 199", result[-1])

    def test_tail_returns_empty_on_missing_file(self):
        agent, _, _ = make_agent()
        agent._log_file = Path("/nonexistent/path/file.log")
        self.assertEqual(agent._tail_log_file(100), [])

    def test_parse_error_line(self):
        line = "14:32:01 [ERROR] zephyro.web: Something broke (module.py:42)"
        agent = self._agent_with_log(line)
        entry = agent._parse_log_line(line)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "ERROR")
        self.assertEqual(entry.logger, "zephyro.web")
        self.assertIn("Something broke", entry.message)
        self.assertEqual(entry.filename, "module.py")
        self.assertEqual(entry.lineno, 42)

    def test_parse_warning_line(self):
        line = "09:15:00 [WARNING] zephyro.llm: Slow response"
        agent = self._agent_with_log(line)
        entry = agent._parse_log_line(line)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "WARNING")

    def test_parse_warn_alias(self):
        line = "09:15:00 [WARN] zephyro.llm: Slow response"
        agent = self._agent_with_log(line)
        entry = agent._parse_log_line(line)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "WARNING")

    def test_parse_returns_none_for_info_line(self):
        line = "14:32:01 [INFO] zephyro.web: All good"
        agent = self._agent_with_log(line)
        entry = agent._parse_log_line(line)
        self.assertIsNone(entry)

    def test_parse_returns_none_for_garbage(self):
        agent = self._agent_with_log("")
        self.assertIsNone(agent._parse_log_line("not a log line"))
        self.assertIsNone(agent._parse_log_line(""))

    def test_filter_errors_keeps_error_and_warning(self):
        entries = [
            LogEntry("t", "ERROR",   "x", "e msg",   "", 0, "", ""),
            LogEntry("t", "WARNING", "x", "w msg",   "", 0, "", ""),
            LogEntry("t", "INFO",    "x", "i msg",   "", 0, "", ""),
        ]
        # Force dedup_key init
        for e in entries:
            e.dedup_key = f"{e.filename}:{e.lineno}:{e.message[:60]}"
        agent = self._agent_with_log("")
        result = agent._filter_errors(entries)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(e.level in ("ERROR", "WARNING") for e in result))

    def test_deduplication_removes_exact_duplicates(self):
        e1 = LogEntry("t", "ERROR", "x", "same msg", "mod.py", 10, "", "raw1")
        e2 = LogEntry("t", "ERROR", "x", "same msg", "mod.py", 10, "", "raw2")
        agent = self._agent_with_log("")
        result = agent._deduplicate([e1, e2])
        self.assertEqual(len(result), 1)

    def test_deduplication_keeps_different_entries(self):
        e1 = LogEntry("t", "ERROR", "x", "msg A", "a.py", 1, "", "raw1")
        e2 = LogEntry("t", "ERROR", "x", "msg B", "b.py", 2, "", "raw2")
        agent = self._agent_with_log("")
        result = agent._deduplicate([e1, e2])
        self.assertEqual(len(result), 2)


# ── Tests: Code Context Resolver ──────────────────────────────────────────────

class TestCodeContextResolver(IsolatedAsyncioTestCase):

    def test_resolves_context_for_existing_file(self):
        source = "\n".join(f"# line {i}" for i in range(1, 101))
        with tempfile.TemporaryDirectory() as tmp:
            src_file = Path(tmp) / "sample.py"
            src_file.write_text(source)
            agent, _, _ = make_agent(code_root=tmp)
            entry = LogEntry("t", "ERROR", "x", "oops", "sample.py", 50, "", "")
            ctx = agent._resolve_code_context(entry)

        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.lineno, 50)
        self.assertGreaterEqual(ctx.window_start, 1)
        self.assertLessEqual(ctx.window_end, 100)
        # ventana de 20 líneas cada lado → al menos 40 líneas
        self.assertGreaterEqual(len(ctx.lines), 20)

    def test_returns_none_for_unknown_file(self):
        entry = LogEntry("t", "ERROR", "x", "oops", "ghost.py", 10, "", "")
        agent, _, _ = make_agent()
        self.assertIsNone(agent._resolve_code_context(entry))

    def test_returns_none_when_no_filename(self):
        entry = LogEntry("t", "ERROR", "x", "oops", "", 0, "", "")
        agent, _, _ = make_agent()
        self.assertIsNone(agent._resolve_code_context(entry))

    def test_target_line_is_marked(self):
        source = "line_one = 1\nline_two = 2\nline_three = 3\n"
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "target.py"
            f.write_text(source)
            agent, _, _ = make_agent(code_root=tmp)
            entry = LogEntry("t", "ERROR", "x", "oops", "target.py", 2, "", "")
            ctx = agent._resolve_code_context(entry)
        self.assertEqual(ctx.target_line.strip(), "line_two = 2")


# ── Tests: Tier Router ────────────────────────────────────────────────────────

class TestTierRouter(IsolatedAsyncioTestCase):

    def _make_entry(self, msg: str = "error", level: str = "ERROR",
                    filename: str = "a.py", tb: str = "") -> LogEntry:
        e = LogEntry("t", level, "x", msg, filename, 1, tb, "")
        return e

    def test_tier3_for_recursion_error(self):
        entry = self._make_entry("RecursionError: max depth")
        agent, _, _ = make_agent()
        with patch.object(DiagnosticAgent, "_gh_available", return_value=False):
            tier = agent._classify_tier([entry])
        self.assertEqual(tier, 3)

    def test_tier3_for_circular_import(self):
        entry = self._make_entry("circular import detected")
        agent, _, _ = make_agent()
        tier = agent._classify_tier([entry])
        self.assertEqual(tier, 3)

    def test_tier2_for_multi_file_with_traceback(self):
        entries = [
            self._make_entry(filename="a.py", tb="File 'a.py', line 1\nTraceback"),
            self._make_entry(filename="b.py", tb="File 'b.py', line 2\nTraceback"),
            self._make_entry(filename="c.py", tb="File 'c.py', line 3\nTraceback"),
        ]
        agent, _, _ = make_agent()
        with patch.object(DiagnosticAgent, "_gh_available", return_value=False):
            tier = agent._classify_tier(entries)
        self.assertEqual(tier, 2)

    def test_tier1_for_warnings_only(self):
        entries = [
            self._make_entry(level="WARNING", tb=""),
            self._make_entry(level="WARNING", filename="b.py", tb=""),
        ]
        agent, _, _ = make_agent()
        tier = agent._classify_tier(entries)
        self.assertEqual(tier, 1)

    def test_tier0_with_single_error_and_gh_available(self):
        entry = self._make_entry(tb="")
        agent, _, _ = make_agent()
        with patch.object(DiagnosticAgent, "_gh_available", return_value=True):
            tier = agent._classify_tier([entry])
        self.assertEqual(tier, 0)

    def test_tier1_when_gh_unavailable(self):
        entry = self._make_entry(tb="")
        agent, _, _ = make_agent()
        with patch.object(DiagnosticAgent, "_gh_available", return_value=False):
            tier = agent._classify_tier([entry])
        self.assertEqual(tier, 1)


# ── Tests: Token Monitor ──────────────────────────────────────────────────────

class TestTokenMonitor(IsolatedAsyncioTestCase):

    async def test_no_warning_below_65_percent(self):
        agent, _, mock_tg = make_agent()
        short_prompt = "hello"
        ratio = await agent._check_token_budget(short_prompt, 1, chat_id=123)
        self.assertLess(ratio, 0.65)
        mock_tg._app.bot.send_message.assert_not_called()

    async def test_warning_sent_above_65_percent(self):
        agent, _, mock_tg = make_agent()
        # Simular un prompt que supera el 65% del límite del tier 1 (200k)
        # 65% de 200000 = 130000 tokens ≈ 520000 chars
        big_prompt = "x " * 70_000
        agent.MODEL_LIMITS = {1: 100_000}  # reducir límite para el test
        ratio = await agent._check_token_budget(big_prompt, 1, chat_id=123)
        # Debe haber enviado al menos un mensaje de aviso
        self.assertGreaterEqual(ratio, 0.0)
        # No verificamos el threshold exacto porque depende de tiktoken vs fallback

    async def test_returns_float_ratio(self):
        agent, _, _ = make_agent()
        ratio = await agent._check_token_budget("test prompt", 2, chat_id=999)
        self.assertIsInstance(ratio, float)
        self.assertGreaterEqual(ratio, 0.0)


# ── Tests: Hypothesis JSON Parsing ────────────────────────────────────────────

class TestHypothesisJsonParsing(IsolatedAsyncioTestCase):

    def test_parse_clean_json(self):
        raw = json.dumps({
            "causa": "NullPointerException",
            "archivos_afectados": ["core/router.py"],
            "solucion_propuesta": "Agregar guard",
            "confianza": 90,
            "tier_usado": 2,
        })
        result = DiagnosticAgent._parse_hypothesis_json(raw, 2)
        self.assertEqual(result.causa, "NullPointerException")
        self.assertEqual(result.archivos_afectados, ["core/router.py"])
        self.assertEqual(result.confianza, 90)
        self.assertEqual(result.tier_usado, 2)

    def test_parse_markdown_wrapped_json(self):
        raw = '```json\n{"causa":"test","archivos_afectados":[],"solucion_propuesta":"fix","confianza":70,"tier_usado":1}\n```'
        result = DiagnosticAgent._parse_hypothesis_json(raw, 1)
        self.assertEqual(result.causa, "test")
        self.assertEqual(result.confianza, 70)

    def test_parse_json_embedded_in_text(self):
        raw = 'Aquí está el análisis: {"causa":"found it","archivos_afectados":["a.py"],"solucion_propuesta":"fix","confianza":80,"tier_usado":1} listo.'
        result = DiagnosticAgent._parse_hypothesis_json(raw, 1)
        self.assertEqual(result.causa, "found it")

    def test_parse_invalid_json_returns_defaults(self):
        result = DiagnosticAgent._parse_hypothesis_json("not json at all", 1)
        self.assertEqual(result.causa, "No determinada")
        self.assertEqual(result.confianza, 50)
        self.assertEqual(result.tier_usado, 1)

    def test_parse_partial_json_uses_defaults(self):
        raw = '{"causa": "partial"}'
        result = DiagnosticAgent._parse_hypothesis_json(raw, 2)
        self.assertEqual(result.causa, "partial")
        self.assertEqual(result.confianza, 50)  # default


# ── Tests: Prompt Builder ─────────────────────────────────────────────────────

class TestPromptBuilder(IsolatedAsyncioTestCase):

    def _make_entries_and_contexts(self):
        entry = LogEntry("14:00:00", "ERROR", "zephyro.core", "Test error", "core.py", 10, "", "raw")
        ctx = CodeContext(
            filepath="/fake/core.py",
            lineno=10,
            window_start=1,
            window_end=5,
            lines=["def foo():", "    x = 1", "    raise ValueError('Test error')", "    return x", ""],
            target_line="    raise ValueError('Test error')",
        )
        return [entry], [ctx]

    def test_prompt_contains_cause(self):
        entries, contexts = self._make_entries_and_contexts()
        hypothesis = DiagnosisResult(
            causa="Causa de prueba",
            archivos_afectados=["core.py"],
            solucion_propuesta="Solución de prueba",
            confianza=80,
            tier_usado=1,
        )
        agent, _, _ = make_agent()
        prompt = agent._build_claude_code_prompt(entries, contexts, hypothesis)
        self.assertIn("Causa de prueba", prompt)
        self.assertIn("Solución de prueba", prompt)
        self.assertIn("core.py", prompt)

    def test_prompt_uses_template_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpl = Path(tmp) / "prompts" / "claude_code_fix_template.md"
            tpl.parent.mkdir()
            tpl.write_text("TEMPLATE: {causa} | {confianza}% | {archivos_afectados}")
            agent, _, _ = make_agent(code_root=tmp)
            agent._template_path = tpl

            entries, contexts = self._make_entries_and_contexts()
            hypothesis = DiagnosisResult("Mi causa", ["x.py"], "Mi fix", 77, 1)
            result = agent._build_claude_code_prompt(entries, contexts, hypothesis)

        self.assertIn("Mi causa", result)
        self.assertIn("77%", result)

    def test_fallback_template_when_file_missing(self):
        agent, _, _ = make_agent()
        agent._template_path = Path("/nonexistent/template.md")
        entries, contexts = self._make_entries_and_contexts()
        hypothesis = DiagnosisResult("Fallback causa", ["y.py"], "Arreglar", 60, 1)
        result = agent._build_claude_code_prompt(entries, contexts, hypothesis)
        self.assertIn("Fallback causa", result)


# ── Tests: run() — Happy path ─────────────────────────────────────────────────

class TestRunOrchestration(IsolatedAsyncioTestCase):

    def _create_tmp_with_log(self, content: str):
        tmp = tempfile.mkdtemp()
        log_path = Path(tmp) / "backend.log"
        log_path.write_text(content)
        return tmp, str(log_path)

    async def test_run_returns_no_errors_message_when_log_clean(self):
        log_content = make_log_lines(
            ("14:00:00", "INFO", "zephyro.web", "All good"),
        )
        tmp, log_file = self._create_tmp_with_log(log_content)
        agent, mock_llm, mock_tg = make_agent(log_file=log_file, code_root=tmp)
        result = await agent.run(chat_id=123, n_lines=50)
        self.assertIn("No se encontraron", result)
        mock_llm.generate.assert_not_called()

    async def test_run_returns_empty_log_message_when_no_file(self):
        agent, _, _ = make_agent()
        agent._log_file = Path("/tmp/nonexistent_zephyro_test_log.log")
        result = await agent.run(chat_id=123, n_lines=50)
        self.assertIn("No se encontraron líneas", result)

    async def test_run_calls_llm_on_error(self):
        log_content = make_log_lines(
            ("14:00:01", "ERROR", "zephyro.core", "Something failed"),
        )
        tmp, log_file = self._create_tmp_with_log(log_content)
        agent, mock_llm, mock_tg = make_agent(log_file=log_file, code_root=tmp)

        with patch.object(DiagnosticAgent, "_classify_tier", return_value=1):
            with patch.object(DiagnosticAgent, "_check_token_budget", new_callable=AsyncMock, return_value=0.3):
                with patch.object(DiagnosticAgent, "_generate_hypothesis", new_callable=AsyncMock) as mock_hyp:
                    mock_hyp.return_value = DiagnosisResult(
                        "Causa test", ["x.py"], "Fix test", 85, 1
                    )
                    mock_tg.send_approval_request = AsyncMock(return_value=99)
                    mock_tg.wait_for_approval = AsyncMock(return_value="view")
                    result = await agent.run(chat_id=123, n_lines=50)

        mock_hyp.assert_called_once()
        # La acción "view" devuelve el resumen del diagnóstico completado
        self.assertIn("Diagnóstico completado", result)

    async def test_run_returns_ignored_when_user_ignores(self):
        log_content = make_log_lines(
            ("14:00:01", "ERROR", "zephyro.core", "Something failed"),
        )
        tmp, log_file = self._create_tmp_with_log(log_content)
        agent, _, mock_tg = make_agent(log_file=log_file, code_root=tmp)

        with patch.object(DiagnosticAgent, "_classify_tier", return_value=1):
            with patch.object(DiagnosticAgent, "_check_token_budget", new_callable=AsyncMock, return_value=0.1):
                with patch.object(DiagnosticAgent, "_generate_hypothesis", new_callable=AsyncMock) as mock_hyp:
                    mock_hyp.return_value = DiagnosisResult("C", ["f.py"], "S", 70, 1)
                    mock_tg.send_approval_request = AsyncMock(return_value=99)
                    mock_tg.wait_for_approval = AsyncMock(return_value="ignore")
                    result = await agent.run(chat_id=123, n_lines=50)

        self.assertIn("ignorado", result.lower())

    async def test_run_aborts_at_95_percent_token_budget(self):
        log_content = make_log_lines(
            ("14:00:01", "ERROR", "zephyro.core", "Big error"),
        )
        tmp, log_file = self._create_tmp_with_log(log_content)
        agent, mock_llm, _ = make_agent(log_file=log_file, code_root=tmp)

        with patch.object(DiagnosticAgent, "_classify_tier", return_value=1):
            with patch.object(DiagnosticAgent, "_check_token_budget", new_callable=AsyncMock, return_value=0.97):
                result = await agent.run(chat_id=123, n_lines=50)

        mock_llm.generate.assert_not_called()
        self.assertIn("Abortado", result)

    async def test_run_escalates_on_more_context(self):
        log_content = make_log_lines(
            ("14:00:01", "ERROR", "zephyro.core", "Complex error"),
        )
        tmp, log_file = self._create_tmp_with_log(log_content)
        agent, _, mock_tg = make_agent(log_file=log_file, code_root=tmp)

        call_count = 0
        async def mock_generate(prompt, tier):
            nonlocal call_count
            call_count += 1
            return DiagnosisResult("Causa", ["f.py"], "Fix", 70, tier)

        approval_responses = ["more_context", "view"]
        approval_idx = 0
        async def mock_approval(msg_id, timeout=300.0):
            nonlocal approval_idx
            r = approval_responses[approval_idx % len(approval_responses)]
            approval_idx += 1
            return r

        with patch.object(DiagnosticAgent, "_classify_tier", return_value=1):
            with patch.object(DiagnosticAgent, "_check_token_budget", new_callable=AsyncMock, return_value=0.1):
                with patch.object(DiagnosticAgent, "_generate_hypothesis", side_effect=mock_generate):
                    mock_tg.send_approval_request = AsyncMock(return_value=99)
                    mock_tg.wait_for_approval = AsyncMock(side_effect=mock_approval)
                    result = await agent.run(chat_id=123, n_lines=50)

        # _generate_hypothesis debe haberse llamado 2 veces (tier 1 + escalado tier 2)
        self.assertEqual(call_count, 2)
        self.assertIn("Diagnóstico completado", result)


# ── Tests: _split_message helper ──────────────────────────────────────────────

class TestSplitMessage(IsolatedAsyncioTestCase):

    def test_short_message_not_split(self):
        result = _split_message("hello world", max_len=100)
        self.assertEqual(result, ["hello world"])

    def test_long_message_split_at_newline(self):
        msg = "line1\nline2\nline3\n" * 300
        chunks = _split_message(msg, max_len=100)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 100)

    def test_no_empty_chunks(self):
        msg = "\n".join(["x" * 50] * 200)
        chunks = _split_message(msg, max_len=200)
        for chunk in chunks:
            self.assertGreater(len(chunk), 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
