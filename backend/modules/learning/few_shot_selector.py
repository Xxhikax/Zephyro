"""
DynamicFewShotSelector — Selecciona ejemplos de buenas respuestas pasadas
para inyectar como few-shot en el prompt actual.

Usa una colección VectorStore separada solo para ejemplos positivos.
"""
import time
import threading
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional


class _EmbeddingError(Exception):
    """embed_query failed: the only few-shot error that implicates Ollama."""


class DynamicFewShotSelector:
    """
    Pool de ejemplos de respuestas buenas (con feedback positivo) para
    inyectar como few-shot en el prompt. Usa el mismo motor de embeddings
    que VectorStore (nomic-embed-text vía Ollama).
    """

    STORAGE_DIR = str(Path(__file__).parent.parent.parent / "storage" / "numpy_db")
    COLLECTION  = "few_shot_examples"

    def __init__(self):
        self._store = None
        self._lock  = threading.Lock()
        self._available = False
        self._init_store()

    def _init_store(self):
        try:
            from modules.memory.vector_store import VectorStore
            self._store = VectorStore(
                collection_name=self.COLLECTION,
                persist_directory=self.STORAGE_DIR,
            )
            self._available = True
        except Exception as e:
            print(f"[FewShot] VectorStore no disponible, few-shot desactivado: {e}")

    # ------------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------------

    def add_example(
        self,
        user_input: str,
        response: str,
        intent: str = "chat",
        importance_score: float = 0.5,
    ):
        """
        Añade un par (pregunta, respuesta) al pool de few-shot.
        Solo debe llamarse cuando la interacción recibió feedback positivo.
        """
        if not self._available or not self._store:
            return
        if len(user_input.strip()) < 10 or len(response.strip()) < 20:
            return
        try:
            # Texto combinado para búsqueda semántica por query
            combined = f"Q: {user_input[:400]}\nA: {response[:800]}"
            self._store.add_memory(
                text=combined,
                metadata={
                    "user_input":  user_input[:500],
                    "response":    response[:2000],
                    "intent":      intent,
                    "importance":  importance_score,
                    "added_at":    time.time(),
                },
                role="few_shot",
                memory_type="fact",
            )
        except Exception as e:
            print(f"[FewShot] Error añadiendo ejemplo: {e}")

    # ------------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------------

    def get_examples(self, query: str, k: int = 2) -> List[Dict[str, Any]]:
        """
        Devuelve hasta k ejemplos semánticamente similares al query.
        Cada elemento: {"user_input", "response", "intent", "score"}
        """
        if not self._available or not self._store:
            return []
        if not query or len(query.strip()) < 5:
            return []

        # Circuit-breaker: esto corre en el path crítico, ANTES de generar la respuesta.
        # Sin el breaker, con Ollama caído cada turno pagaba los reintentos largos de
        # embed_query (ver core/ollama_health: ~90s de overhead por turno). El guard de
        # longitud de arriba no alcanza — "1, 2, 3, probando." tiene 18 caracteres.
        from core.ollama_health import is_ollama_up, mark_ollama_down
        if not is_ollama_up():
            return []

        try:
            return self._search_with_metadata(query, k)
        except _EmbeddingError as e:
            # Only an embedding failure says something about Ollama.
            mark_ollama_down()
            print(f"[FewShot] Error de embedding: {e}")
            return []
        except Exception as e:
            # faiss/numpy errors (dimension mismatch, corrupt store) are local:
            # tripping the global breaker would skip every Ollama consumer for 15 s.
            print(f"[FewShot] Error en búsqueda: {e}")
            return []

    def _search_with_metadata(self, query: str, k: int) -> List[Dict[str, Any]]:
        store = self._store
        with store.lock:
            if not store.vectors:
                return []
            docs_snap  = list(store.documents)
            metas_snap = list(store.metadatas)
            vecs_snap  = list(store.vectors)

        try:
            query_vec = store.embedding_function.embed_query(query)
        except Exception as e:
            raise _EmbeddingError(str(e)) from e

        try:
            import faiss as _faiss
            vecs = np.array(vecs_snap, dtype=np.float32)
            q    = np.array([query_vec], dtype=np.float32)
            _faiss.normalize_L2(vecs)
            _faiss.normalize_L2(q)
            n = min(k, len(docs_snap))
            index = _faiss.IndexFlatIP(vecs.shape[1])
            index.add(vecs)
            scores, indices = index.search(q, n)
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(docs_snap):
                    continue
                if score < 0.60:
                    continue
                meta = metas_snap[idx] if idx < len(metas_snap) else {}
                results.append({
                    "user_input": meta.get("user_input", ""),
                    "response":   meta.get("response",   ""),
                    "intent":     meta.get("intent",     "chat"),
                    "score":      float(score),
                })
            return results
        except ImportError:
            pass

        # Fallback NumPy
        vecs = np.array(vecs_snap)
        q    = np.array(query_vec)
        norms_v = np.linalg.norm(vecs, axis=1)
        norm_q  = np.linalg.norm(q)
        if norm_q == 0:
            return []
        valid = norms_v > 0
        if not np.any(valid):
            return []
        sims = np.dot(vecs[valid], q) / (norms_v[valid] * norm_q)
        valid_idx = np.where(valid)[0]
        top_local = np.argsort(sims)[-k:][::-1]
        results = []
        for li in top_local:
            score = sims[li]
            if score < 0.60:
                continue
            idx = valid_idx[li]
            meta = metas_snap[idx] if idx < len(metas_snap) else {}
            results.append({
                "user_input": meta.get("user_input", ""),
                "response":   meta.get("response",   ""),
                "intent":     meta.get("intent",     "chat"),
                "score":      float(score),
            })
        return results

    # ------------------------------------------------------------------
    # Formateo para prompt
    # ------------------------------------------------------------------

    def format_for_prompt(self, examples: List[Dict[str, Any]], max_chars: int = 800) -> str:
        """Formatea los ejemplos para inyectar en el prompt del LLM."""
        if not examples:
            return ""
        lines = ["EJEMPLOS DE RESPUESTAS SIMILARES (referencia de estilo y contenido):"]
        total = 0
        for ex in examples:
            u = ex.get("user_input", "")[:150]
            r = ex.get("response",   "")[:300]
            block = f"Bruno: {u}\nZephyro: {r}"
            if total + len(block) > max_chars:
                break
            lines.append(block)
            total += len(block)
        if len(lines) == 1:
            return ""
        lines.append("")
        return "\n".join(lines)

    def count(self) -> int:
        if not self._available or not self._store:
            return 0
        try:
            with self._store.lock:
                return len(self._store.documents)
        except Exception:
            return 0
