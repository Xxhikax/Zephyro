# tests/test_search_list_queries.py
"""
Tests para el pipeline de búsqueda de queries tipo lista/enumeración.

Cubre:
  - Detección de list-intent (_is_list_query)
  - Reescritura de queries imperativas/electorales (_rewrite_query)
  - execute_search activa deep=True y max_results≥10 en list-queries
  - execute_search hace fetch_article para top-2 resultados
  - generate_with_tools hace síntesis final cuando itera sin end_turn
  - Orchestrator cae a search_web_direct cuando tool loop devuelve sentinel
  - Regresión: queries que NO deben disparar web search
"""
from __future__ import annotations

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# Asegurar que el package raíz de backend esté en el path
_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ──────────────────────────────────────────────────────────────────────────────
# Importar helpers a testear (no instantiate heavy deps)
# ──────────────────────────────────────────────────────────────────────────────

from modules.search.tools import (
    _is_list_query,
    _rewrite_query,
    execute_search,
)


# ════════════════════════════════════════════════════════════════════════════
# 1. _is_list_query
# ════════════════════════════════════════════════════════════════════════════

class TestIsListQuery:
    """_is_list_query debe detectar queries que piden listas/enumeraciones."""

    @pytest.mark.parametrize("query", [
        "candidatos presidenciales Peru 2026 lista completa JNE",
        "dame la lista de candidatos presidenciales del Perú",
        "lista de candidatos a la presidencia del Perú",
        "cuáles son los candidatos presidenciales inscritos en el JNE",
        "todos los candidatos presidenciales 2026",
        "listame los postulantes presidenciales",
        "enumera los candidatos inscritos",
        "cuántos candidatos hay para las elecciones presidenciales",
        "ranking de candidatos según encuesta",
        "inscritos para la presidencia Peru JNE",
    ])
    def test_is_list_query_true(self, query: str):
        assert _is_list_query(query), f"Expected list-query for: {query!r}"

    @pytest.mark.parametrize("query", [
        "¿Qué es Python?",
        "quién ganó las elecciones presidenciales de 2021 en Perú",
        "precio bitcoin",
        "hola",
        "42 × 7",
        "llueve hoy en Lima",
        "explica la relatividad",
    ])
    def test_is_list_query_false(self, query: str):
        assert not _is_list_query(query), f"Expected NOT list-query for: {query!r}"


# ════════════════════════════════════════════════════════════════════════════
# 2. _rewrite_query
# ════════════════════════════════════════════════════════════════════════════

class TestRewriteQuery:
    """_rewrite_query debe simplificar queries imperativas y añadir contexto electoral."""

    def test_election_candidates_peru_rewrites(self):
        q = "Dame la lista de los 35 candidatos presidenciales del Perú"
        result = _rewrite_query(q)
        assert "candidatos presidenciales" in result.lower()
        assert "peru" in result.lower() or "Perú" in result or "Peru" in result
        # Debe incluir año y referencia electoral
        assert any(yr in result for yr in ["2025", "2026", "2027"]), \
            f"Expected year in rewritten query: {result!r}"
        # No debe empezar con "Dame"
        assert not result.lower().startswith("dame"), \
            f"Imperative prefix not stripped: {result!r}"

    def test_imperative_prefix_stripped(self):
        q = "dame los candidatos presidenciales de Chile 2026"
        result = _rewrite_query(q)
        assert not result.lower().startswith("dame")

    def test_listame_prefix_stripped(self):
        q = "listame los candidatos presidenciales del Perú"
        result = _rewrite_query(q)
        assert not result.lower().startswith("listame")

    def test_no_rewrite_for_clean_query(self):
        q = "candidatos presidenciales Peru 2026 JNE lista"
        result = _rewrite_query(q)
        # Clean query should not be mangled
        assert "candidatos" in result.lower()
        assert "peru" in result.lower() or "Peru" in result

    def test_timeless_query_unchanged(self):
        q = "¿Qué es la fotosíntesis?"
        result = _rewrite_query(q)
        # Should not be changed (no election keywords)
        assert result == q or result.lower() == q.lower()

    def test_non_peru_country_rewrite(self):
        """Rewrite works for other countries too."""
        q = "dame la lista de candidatos presidenciales de Colombia"
        result = _rewrite_query(q)
        assert "colombia" in result.lower()
        assert not result.lower().startswith("dame")


# ════════════════════════════════════════════════════════════════════════════
# 3. execute_search — list-intent activa max_results y deep mode
# ════════════════════════════════════════════════════════════════════════════

class TestExecuteSearchListIntent:
    """execute_search debe detectar list-intent y ajustar parámetros."""

    @pytest.mark.asyncio
    async def test_list_query_bumps_max_results(self):
        """max_results debe subir a ≥10 para list-queries."""
        mock_searcher = MagicMock()
        mock_searcher.search = AsyncMock(return_value=[
            MagicMock(title="Test", url="http://test.com", snippet="snippet", full_text="")
        ])
        mock_searcher.fetch_article = AsyncMock(return_value="article text")

        query = "candidatos presidenciales Peru 2026 lista completa JNE"
        await execute_search(query, max_results=5, searcher=mock_searcher)

        # search debe haberse llamado con max_results >= 10
        _, kwargs = mock_searcher.search.call_args
        actual_max = kwargs.get("max_results", mock_searcher.search.call_args[0][1] if len(mock_searcher.search.call_args[0]) > 1 else 5)
        assert actual_max >= 10, f"Expected max_results≥10 for list query, got {actual_max}"

    @pytest.mark.asyncio
    async def test_list_query_enables_deep_mode(self):
        """deep=True debe pasarse para list-queries."""
        mock_searcher = MagicMock()
        mock_searcher.search = AsyncMock(return_value=[
            MagicMock(title="Test", url="http://test.com", snippet="snippet", full_text="")
        ])
        mock_searcher.fetch_article = AsyncMock(return_value="article text")

        query = "lista de candidatos presidenciales inscritos Peru JNE 2026"
        await execute_search(query, max_results=5, searcher=mock_searcher)

        # deep=True debe estar en el call
        _, kwargs = mock_searcher.search.call_args
        assert kwargs.get("deep") is True, \
            f"Expected deep=True for list query, got deep={kwargs.get('deep')}"

    @pytest.mark.asyncio
    async def test_list_query_fetches_article_for_top_results(self):
        """Para list-queries, execute_search debe llamar fetch_article para top resultados."""
        r1 = MagicMock(title="JNE Candidatos", url="http://jne.gob.pe/candidatos", snippet="Lista JNE", full_text="")
        r2 = MagicMock(title="Peru21 Candidatos", url="http://peru21.pe/candidatos", snippet="36 candidatos", full_text="")
        mock_searcher = MagicMock()
        mock_searcher.search = AsyncMock(return_value=[r1, r2])
        mock_searcher.fetch_article = AsyncMock(return_value="Contenido completo del artículo con lista de candidatos...")

        query = "lista de candidatos presidenciales Peru 2026 JNE"
        result = await execute_search(query, max_results=10, searcher=mock_searcher)

        # fetch_article debe haberse llamado al menos una vez
        assert mock_searcher.fetch_article.call_count >= 1, \
            "fetch_article should be called for top results on list queries"
        # El resultado debe incluir texto del artículo
        assert "Contenido completo" in result or "candidatos" in result.lower()

    @pytest.mark.asyncio
    async def test_non_list_query_no_deep(self):
        """Queries no-lista no deben activar deep mode."""
        mock_searcher = MagicMock()
        mock_searcher.search = AsyncMock(return_value=[
            MagicMock(title="Python", url="http://python.org", snippet="Python lang", full_text="")
        ])
        mock_searcher.fetch_article = AsyncMock(return_value="")

        await execute_search("¿qué es Python?", max_results=5, searcher=mock_searcher)

        _, kwargs = mock_searcher.search.call_args
        assert kwargs.get("deep") is not True, \
            "deep should not be True for non-list queries"

    @pytest.mark.asyncio
    async def test_non_list_query_does_not_bump_max_results(self):
        """Queries no-lista no deben subir max_results."""
        mock_searcher = MagicMock()
        mock_searcher.search = AsyncMock(return_value=[
            MagicMock(title="r", url="http://x.com", snippet="s", full_text="")
        ])
        mock_searcher.fetch_article = AsyncMock(return_value="")

        await execute_search("precio del dólar hoy", max_results=5, searcher=mock_searcher)

        _, kwargs = mock_searcher.search.call_args
        actual_max = kwargs.get("max_results", 5)
        assert actual_max == 5, f"max_results should stay 5 for non-list query, got {actual_max}"

    @pytest.mark.asyncio
    async def test_returns_no_results_message_on_empty(self):
        """Si todas las capas retornan vacío, debe retornar mensaje útil."""
        mock_searcher = MagicMock()
        mock_searcher.search = AsyncMock(return_value=[])

        result = await execute_search("lista candidatos presidenciales Peru 2026", searcher=mock_searcher)
        assert "No se encontraron" in result


# ════════════════════════════════════════════════════════════════════════════
# 4. generate_with_tools — síntesis final cuando loop se agota
# ════════════════════════════════════════════════════════════════════════════

# Simulate Anthropic block types accurately (no MagicMock auto-attrs)
class _ToolUseBlock:
    """Simulate anthropic ToolUseBlock — has NO .text attribute."""
    type = "tool_use"
    def __init__(self, block_id: str = "tu_1", name: str = "search", input: dict | None = None):
        self.id = block_id
        self.name = name
        self.input = input or {"query": "q"}

class _TextBlock:
    """Simulate anthropic TextBlock — has .text, .type='text'."""
    type = "text"
    def __init__(self, text: str):
        self.text = text


class TestGenerateWithToolsSynthesisFallback:
    """generate_with_tools debe intentar síntesis final sin tools cuando agota iteraciones."""

    def _make_tool_use_response(self, block_id: str = "tu_1"):
        resp = MagicMock()
        resp.stop_reason = "tool_use"
        resp.content = [_ToolUseBlock(block_id=block_id)]
        return resp

    def _make_synthesis_response(self, text: str):
        resp = MagicMock()
        resp.stop_reason = "end_turn"
        resp.content = [_TextBlock(text)]
        return resp

    def test_synthesis_called_when_iterations_exhausted(self):
        """Cuando loop agota, debe intentar llamada de síntesis sin tools."""
        import asyncio as _asyncio
        sys.path.insert(0, _BACKEND)
        from core.llm_manager import LLMManager

        tool_resp = self._make_tool_use_response()
        synth_text = "Lista de candidatos presidenciales: 1. Keiko Fujimori..."
        synth_resp = self._make_synthesis_response(synth_text)

        responses = [tool_resp, tool_resp, synth_resp]
        call_count = [0]

        def create_side_effect(**kwargs):
            resp = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return resp

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = create_side_effect

        async def dummy_tool_executor(name, inp):
            return "search result text"

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = LLMManager.generate_with_tools(
                    MagicMock(anthropic_model="claude-haiku-4-5"),
                    system_prompt="system",
                    user_message="Dame la lista de candidatos",
                    tools=[{"name": "search"}],
                    tool_executor=dummy_tool_executor,
                    max_iterations=2,
                )

        # La síntesis (sin tools) debe haberse intentado
        calls = mock_client.messages.create.call_args_list
        synthesis_calls = [c for c in calls if "tools" not in (c.kwargs or {})]
        assert len(synthesis_calls) >= 1, \
            "At least one synthesis call (without tools) should have been made"

    def test_no_sentinel_returned_when_synthesis_succeeds(self):
        """El sentinel de error no debe llegar al usuario si la síntesis tiene éxito."""
        SENTINEL = "No se pudo completar la respuesta con las herramientas disponibles."

        tool_resp = self._make_tool_use_response()
        synth_resp = self._make_synthesis_response("Respuesta de síntesis con datos concretos 1 2 3")

        responses = [tool_resp, tool_resp, synth_resp]
        call_count = [0]

        def create_side(*a, **kw):
            r = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return r

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = create_side

        async def dummy_exec(name, inp):
            return "search results"

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            with patch("anthropic.Anthropic", return_value=mock_client):
                from core.llm_manager import LLMManager
                result = LLMManager.generate_with_tools(
                    MagicMock(anthropic_model="claude-haiku-4-5"),
                    system_prompt="sys",
                    user_message="lista candidatos",
                    tools=[{"name": "search"}],
                    tool_executor=dummy_exec,
                    max_iterations=2,
                )

        assert result != SENTINEL, f"Sentinel should not be returned when synthesis succeeds"
        assert isinstance(result, str) and len(result) > 10, \
            f"Expected a non-empty string result, got: {result!r}"


# ════════════════════════════════════════════════════════════════════════════
# 5. Needs-web-search classifier regression
# ════════════════════════════════════════════════════════════════════════════

class TestNeedsWebSearchRegression:
    """Regresión: los patterns existentes no deben romperse."""

    def _fast_search(self, q: str) -> bool:
        """Aplica solo Layer 0 + Layer A (sin LLM) para tests síncronos rápidos."""
        import re
        from modules.integrations.api_registry import (
            _ALWAYS_SEARCH_PATTERNS, _NEVER_SEARCH_PATTERNS
        )
        q_lower = q.strip().lower()
        for p in _ALWAYS_SEARCH_PATTERNS:
            if re.search(p, q_lower):
                return True
        if len(q_lower.split()) <= 3:
            return False
        for p in _NEVER_SEARCH_PATTERNS:
            if re.search(p, q_lower):
                return False
        return None  # type: ignore  # Layer B (LLM) needed — skip in unit test

    @pytest.mark.parametrize("query", [
        "dame la lista de los 35 candidatos presidenciales del Perú",
        "candidatos presidenciales Peru 2026",
        "quiénes son los candidatos para las elecciones presidenciales",
        "cuántos candidatos hay para las elecciones de Peru",
        "presidencial elecciones Peru",
    ])
    def test_election_queries_always_search(self, query: str):
        result = self._fast_search(query)
        assert result is True, f"Expected always-search for: {query!r}, got {result}"

    @pytest.mark.parametrize("query", [
        "hola",
        "buenos días",
        "quién eres",
    ])
    def test_greetings_never_search(self, query: str):
        result = self._fast_search(query)
        assert result is not True, f"Greeting should not trigger search: {query!r}"
