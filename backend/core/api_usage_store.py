"""
api_usage_store — Persistencia de uso de APIs cloud (tokens, costo, latencia).

Cada llamada a un provider cloud (Anthropic, DeepSeek, OpenAI, Gemini) se
registra aquí para alimentar el panel de gestión de APIs. Persiste en
`storage/api_usage.db` (SQLite). Diseño thread-safe con RLock, mismo patrón
que ScheduleService y ProviderConfig.

El costo se estima con `pricing_table.estimate_cost()`. Las llamadas locales
(Ollama) se registran con costo 0 para tener conteo de requests sin inflar gasto.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger("zephyro.api_usage")


class APIUsageStore:
    """Registro append-only de llamadas a APIs cloud + agregaciones por periodo."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = storage_path or Path("storage/api_usage.db")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.storage_path))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    latency_ms INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0,
                    success INTEGER DEFAULT 1,
                    error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_calls_ts ON api_calls(ts)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_calls_provider ON api_calls(provider)"
            )

    # ── Write ────────────────────────────────────────────────────────

    def record(
        self,
        provider: str,
        model: Optional[str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        cost_usd: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Registra una llamada. Nunca lanza — el tracking no debe romper el flujo."""
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO api_calls "
                    "(ts, provider, model, tokens_in, tokens_out, latency_ms, cost_usd, success, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        time.time(), provider, model,
                        int(tokens_in or 0), int(tokens_out or 0),
                        int(latency_ms or 0), float(cost_usd or 0.0),
                        1 if success else 0, error,
                    ),
                )
        except Exception as exc:
            log.debug("[api_usage] record failed: %s", exc)

    # ── Read ─────────────────────────────────────────────────────────

    @staticmethod
    def _period_start(period: str) -> float:
        now = datetime.now()
        if period == "today":
            return datetime.combine(now.date(), dtime.min).timestamp()
        if period == "week":
            return (now - timedelta(days=7)).timestamp()
        if period == "month":
            return (now - timedelta(days=30)).timestamp()
        return 0.0  # "all"

    def get_stats(self, period: str = "today") -> dict[str, dict[str, Any]]:
        """Agrega uso por provider en el periodo dado.

        Returns: { provider: {requests, tokens_in, tokens_out, cost_usd,
                               avg_latency_ms, errors, last_used_ts} }
        """
        start = self._period_start(period)
        out: dict[str, dict[str, Any]] = {}
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT provider,
                           COUNT(*)                AS requests,
                           SUM(tokens_in)          AS tokens_in,
                           SUM(tokens_out)         AS tokens_out,
                           SUM(cost_usd)           AS cost_usd,
                           AVG(latency_ms)         AS avg_latency_ms,
                           SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS errors,
                           MAX(ts)                 AS last_used_ts
                    FROM api_calls
                    WHERE ts >= ?
                    GROUP BY provider
                    """,
                    (start,),
                ).fetchall()
            for r in rows:
                out[r[0]] = {
                    "requests": r[1] or 0,
                    "tokens_in": r[2] or 0,
                    "tokens_out": r[3] or 0,
                    "cost_usd": round(r[4] or 0.0, 6),
                    "avg_latency_ms": int(r[5] or 0),
                    "errors": r[6] or 0,
                    "last_used_ts": r[7],
                }
        except Exception as exc:
            log.debug("[api_usage] get_stats failed: %s", exc)
        return out

    def get_log(
        self, provider: Optional[str] = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Últimas llamadas registradas, opcionalmente filtradas por provider."""
        rows: list[Any] = []
        try:
            with self._lock, self._connect() as conn:
                if provider:
                    rows = conn.execute(
                        "SELECT ts, provider, model, tokens_in, tokens_out, "
                        "latency_ms, cost_usd, success, error "
                        "FROM api_calls WHERE provider=? ORDER BY ts DESC LIMIT ?",
                        (provider, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT ts, provider, model, tokens_in, tokens_out, "
                        "latency_ms, cost_usd, success, error "
                        "FROM api_calls ORDER BY ts DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        except Exception as exc:
            log.debug("[api_usage] get_log failed: %s", exc)

        return [
            {
                "ts": r[0],
                "provider": r[1],
                "model": r[2],
                "tokens_in": r[3],
                "tokens_out": r[4],
                "latency_ms": r[5],
                "cost_usd": round(r[6] or 0.0, 6),
                "success": bool(r[7]),
                "error": r[8],
            }
            for r in rows
        ]


# ── LangChain usage extraction ──────────────────────────────────────────

def extract_langchain_usage(response: Any, provider: str) -> tuple[int, int]:
    """Extrae (tokens_in, tokens_out) de un AIMessage de LangChain.

    El formato de response_metadata varía por provider:
      - Anthropic → usage.input_tokens / output_tokens
      - OpenAI/DeepSeek → token_usage.prompt_tokens / completion_tokens
      - Gemini → usage_metadata.prompt_token_count / candidates_token_count

    Devuelve (0, 0) si no encuentra metadata (nunca lanza).
    """
    try:
        # LangChain >=0.2 expone usage_metadata uniforme en el AIMessage
        um = getattr(response, "usage_metadata", None)
        if isinstance(um, dict) and um:
            ti = um.get("input_tokens") or um.get("prompt_tokens") or 0
            to = um.get("output_tokens") or um.get("completion_tokens") or 0
            if ti or to:
                return int(ti), int(to)

        meta = getattr(response, "response_metadata", None) or {}
        if not isinstance(meta, dict):
            return 0, 0

        # Anthropic
        usage = meta.get("usage")
        if isinstance(usage, dict):
            ti = usage.get("input_tokens", 0)
            to = usage.get("output_tokens", 0)
            if ti or to:
                return int(ti), int(to)

        # OpenAI / DeepSeek
        tu = meta.get("token_usage")
        if isinstance(tu, dict):
            ti = tu.get("prompt_tokens", 0)
            to = tu.get("completion_tokens", 0)
            if ti or to:
                return int(ti), int(to)

        # Gemini
        gm = meta.get("usage_metadata")
        if isinstance(gm, dict):
            ti = gm.get("prompt_token_count", 0)
            to = gm.get("candidates_token_count", 0)
            if ti or to:
                return int(ti), int(to)
    except Exception:
        pass
    return 0, 0


# ── Pricing key resolution ──────────────────────────────────────────────

# Mapea el provider interno de LLMManager → prefijo del pricing_table.
_PROVIDER_PRICING_ALIAS = {
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "openai": "openai",
    "gemini": "gemini",
    "local": "ollama_local",
}


def estimate_call_cost(
    provider: str, model: Optional[str], tokens_in: int, tokens_out: int
) -> float:
    """Wrapper sobre pricing_table.estimate_cost que normaliza el nombre de provider."""
    if not model:
        return 0.0
    try:
        from modules.code_analysis.pricing_table import estimate_cost
        pricing_provider = _PROVIDER_PRICING_ALIAS.get(provider, provider)
        # pricing usa la última parte del modelo (sin prefijo provider/)
        model_key = model.split("/")[-1] if "/" in model else model
        return estimate_cost(pricing_provider, model_key, tokens_in, tokens_out)
    except Exception:
        return 0.0


# ── Singleton ───────────────────────────────────────────────────────────

_instance: Optional[APIUsageStore] = None
_instance_lock = threading.Lock()


def get_usage_store() -> APIUsageStore:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = APIUsageStore()
    return _instance
