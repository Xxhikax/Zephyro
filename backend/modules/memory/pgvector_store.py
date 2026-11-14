import os
import threading
import time
import uuid
import json
from typing import Optional, Dict, List

import psycopg2
import psycopg2.pool
from psycopg2.extras import Json
from pgvector.psycopg2 import register_vector
from langchain_community.embeddings import OllamaEmbeddings  # deprecated, remove after migration
try:
    from langchain_ollama import OllamaEmbeddings as _OllamaEmb
    OllamaEmbeddings = _OllamaEmb  # noqa: F811
except ImportError:
    pass

# Leer conexión desde env vars
DB_HOST = os.getenv("PGHOST", "localhost")
DB_PORT = os.getenv("PGPORT", "5432")
DB_NAME = os.getenv("PGDATABASE", "zephyro_db")
DB_USER = os.getenv("PGUSER", "zephyro_user")
DB_PASS = os.getenv("PGPASSWORD", "")

# Connection pool — compartido entre todas las instancias del proceso
_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()

def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2, maxconn=10,
                    host=DB_HOST, port=DB_PORT,
                    dbname=DB_NAME, user=DB_USER, password=DB_PASS,
                )
    return _pool

def _get_conn():
    """Obtener conexión del pool y configurarla."""
    conn = _get_pool().getconn()
    conn.autocommit = True
    register_vector(conn)
    return conn

def _put_conn(conn):
    """Devolver conexión al pool."""
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass

class PGVectorStore:
    def __init__(self, table_name="memories", embedding_model=None):
        import os
        self.table_name = table_name
        model = embedding_model or os.getenv("ZEPHYRO_EMBED_MODEL", "nomic-embed-text")
        self.embedding = OllamaEmbeddings(model=model)
        self._ensure_table()

    def _ensure_table(self):
        conn = _get_conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception:
                pass
            dim = 768  # nomic-embed-text produce vectores de 768 dimensiones
            create_sql = f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id UUID PRIMARY KEY,
                content TEXT,
                embedding VECTOR({dim}),
                role TEXT,
                timestamp TIMESTAMPTZ,
                metadata JSONB,
                memory_type TEXT
            );
            """
            cur.execute(create_sql)
            try:
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_embedding ON {self.table_name} USING ivfflat (embedding) WITH (lists = 100);")
            except Exception:
                pass
            cur.close()
        finally:
            _put_conn(conn)

    def add_memory(self, text: str, metadata: Optional[Dict] = None, role: str = "user", memory_type: str = "conversation"):
        """
        memory_type puede ser: 
        - 'conversation': Charlas normales
        - 'fact': Información importante (nombre, gustos, datos)
        - 'task': Tareas pendientes o automatizaciones
        """
        if not text: return
        if metadata is None: metadata = {}
        
        # FILTRO 1: Excluir alucinaciones sobre software irrelevante
        hallucination_keywords = ["libreoffice", "calc", "excel", "openoffice", "para abrir"]
        if role == "assistant" and any(kw in text.lower() for kw in hallucination_keywords):
            # print(f"[Memory] BLOQUEADO: Alucinación detectada - {text[:50]}...")
            return  # NO GUARDAR
        
        # FILTRO 2: Detectar importancia automáticamente por palabras clave
        important_keywords = ["recuerda", "mi nombre", "vivo en", "estudio", "tarea", "pendiente", "favorito", "importante"]
        if any(kw in text.lower() for kw in important_keywords):
            memory_type = "fact"
            metadata["priority"] = "high"
            print(f"[Memory] Guardando dato importante: {text[:40]}...")

        # El resto del worker se encarga de insertar con este memory_type
        thread = threading.Thread(target=self._insert_worker, args=(text, metadata, role, memory_type))
        thread.daemon = True
        thread.start()

    def _insert_worker(self, text, metadata, role, memory_type):
        from core.ollama_health import is_ollama_up, mark_ollama_down
        if not is_ollama_up():
            return  # Ollama down: skip embedding (avoids retry-spam in the bg thread)
        try:
            vec = list(self.embedding.embed_query(text))
            conn = _get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO {self.table_name} (id, content, embedding, role, timestamp, metadata, memory_type) VALUES (%s,%s,%s::vector,%s, NOW(), %s, %s)",
                    (str(uuid.uuid4()), text, vec, role, Json(metadata), memory_type)
                )
                cur.close()
            finally:
                _put_conn(conn)
        except Exception as e:
            mark_ollama_down()
            print(f"[PGMemory] Insert error: {e}")

    def search_relevant_context(self, query: str, k: int = 3, similarity_threshold: float = 0.6) -> str:
        """Busca contexto relevante con umbral de similitud y prioridad por tipo (fact/task > conversation)."""
        # Circuit-breaker: la búsqueda corre en el path crítico (antes de generar). Si Ollama
        # (embeddings) no responde, no intentar — evita reintentos largos por turno.
        from core.ollama_health import is_ollama_up, mark_ollama_down
        if not is_ollama_up():
            return ""
        try:
            qvec = list(self.embedding.embed_query(query))
            conn = _get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    SELECT content, role FROM (
                        SELECT content, role, memory_type,
                               (embedding <=> %s::vector) AS distance
                        FROM {self.table_name}
                        WHERE (embedding <=> %s::vector) <= %s
                    ) sub
                    ORDER BY
                        CASE memory_type WHEN 'fact' THEN 0 WHEN 'task' THEN 1 ELSE 2 END ASC,
                        distance ASC
                    LIMIT %s
                    """,
                    (qvec, qvec, similarity_threshold, k)
                )
                rows = cur.fetchall()
                cur.close()
            finally:
                _put_conn(conn)
            if not rows:
                return ""
            parts = []
            for content, role in rows:
                parts.append(f"- [{role or 'unknown'}]: {content.strip()}")
            return "\n".join(parts)
        except Exception as e:
            mark_ollama_down()
            print(f"[PGMemory] Search error: {e}")
            return ""

    def count(self) -> int:
        try:
            conn = _get_conn()
            try:
                cur = conn.cursor()
                cur.execute(f"SELECT count(1) FROM {self.table_name}")
                n = cur.fetchone()[0]
                cur.close()
            finally:
                _put_conn(conn)
            return int(n)
        except Exception:
            return 0

    def get_memory_count(self) -> int:
        return self.count()