"""Pool compartido de Postgres para módulos transaccionales (fitness, nutrition, etc.).

Wrapper sobre psycopg2.pool.ThreadedConnectionPool con context managers helpers.
Reusa las mismas env vars que pgvector_store: PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD.

Patrón clonado de backend/modules/memory/pgvector_store.py pero sin acoplamiento a
pgvector (no llama register_vector) — usable para tablas relacionales puras.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg2
import psycopg2.pool

log = logging.getLogger("zephyro.postgres_pool")

DB_HOST = os.getenv("PGHOST", "localhost")
DB_PORT = os.getenv("PGPORT", "5432")
DB_NAME = os.getenv("PGDATABASE", "zephyro_db")
DB_USER = os.getenv("PGUSER", "zephyro_user")
DB_PASS = os.getenv("PGPASSWORD", "")

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Devuelve el pool compartido del proceso. Lazy init thread-safe."""
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2, maxconn=10,
                    host=DB_HOST, port=DB_PORT,
                    dbname=DB_NAME, user=DB_USER, password=DB_PASS,
                )
                log.info("[pg_pool] inicializado (%s:%s/%s)", DB_HOST, DB_PORT, DB_NAME)
    return _pool


@contextmanager
def get_conn(autocommit: bool = True) -> Iterator[psycopg2.extensions.connection]:
    """Context manager: obtiene conexión, la devuelve al pool al salir."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = autocommit
        yield conn
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass


@contextmanager
def get_cursor(autocommit: bool = True) -> Iterator[psycopg2.extensions.cursor]:
    """Context manager: conexión + cursor en un solo with."""
    with get_conn(autocommit=autocommit) as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def close_pool() -> None:
    """Cierra el pool. Solo para shutdown ordenado del proceso o tests."""
    global _pool
    with _pool_lock:
        if _pool is not None and not _pool.closed:
            try:
                _pool.closeall()
            except Exception:
                pass
        _pool = None
