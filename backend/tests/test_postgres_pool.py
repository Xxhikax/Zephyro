"""Smoke + env tests para backend/core/postgres_pool.py."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _postgres_reachable() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv("PGHOST", "localhost"),
            port=os.getenv("PGPORT", "5432"),
            dbname=os.getenv("PGDATABASE", "zephyro_db"),
            user=os.getenv("PGUSER", "zephyro_user"),
            password=os.getenv("PGPASSWORD", ""),
            connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


HAS_PG = _postgres_reachable()


class TestPostgresPoolModule:
    def test_module_imports(self):
        from core import postgres_pool
        assert hasattr(postgres_pool, "get_pool")
        assert hasattr(postgres_pool, "get_conn")
        assert hasattr(postgres_pool, "get_cursor")
        assert hasattr(postgres_pool, "close_pool")

    def test_env_var_defaults_present(self):
        from core import postgres_pool
        assert postgres_pool.DB_HOST  # at least a default
        assert postgres_pool.DB_NAME


@pytest.mark.skipif(not HAS_PG, reason="Postgres no disponible — skip integration")
class TestPostgresPoolIntegration:
    def test_get_pool_returns_pool(self):
        from core.postgres_pool import get_pool
        pool = get_pool()
        assert pool is not None

    def test_get_conn_context_manager(self):
        from core.postgres_pool import get_conn
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
            cur.close()

    def test_get_cursor_context_manager(self):
        from core.postgres_pool import get_cursor
        with get_cursor() as cur:
            cur.execute("SELECT 42")
            assert cur.fetchone()[0] == 42
