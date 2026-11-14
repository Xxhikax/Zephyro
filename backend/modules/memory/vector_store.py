import os
import json
import numpy as np
import time
import threading
from langchain_community.embeddings import OllamaEmbeddings  # deprecated, remove after migration
try:
    from langchain_ollama import OllamaEmbeddings as _OllamaEmb
    OllamaEmbeddings = _OllamaEmb  # noqa: F811
except ImportError:
    pass
from typing import List, Dict, Optional

# FAISS para búsqueda vectorial acelerada (opcional)
try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False

class VectorStore:
    def __init__(self, collection_name="zephyro_memory", persist_directory="./storage/numpy_db"):
        self.persist_path = os.path.join(persist_directory, f"{collection_name}.json")
        self.persist_directory = persist_directory
        
        # Modelo de embeddings (configurable; default nomic-embed-text → 768 dims).
        self.embedding_function = OllamaEmbeddings(
            model=os.getenv("ZEPHYRO_EMBED_MODEL", "nomic-embed-text")
        )
        
        # Almacenamiento en memoria
        self.documents: List[str] = []
        self.metadatas: List[Dict] = []
        self.vectors: List[List[float]] = []
        
        # FAISS index (se construye al cargar o al añadir vectores)
        self._faiss_index = None
        self._embedding_dim: Optional[int] = None
        
        # Control de concurrencia
        self.lock = threading.Lock()
        
        # Asegurar directorio
        os.makedirs(self.persist_directory, exist_ok=True)
        self._load()
        
        # Estadísticas
        count = len(self.documents)
        backend = "FAISS" if HAS_FAISS else "NumPy"
        print(f"[Memory] Sistema de Memoria Persistente ({backend}) activo.")
        print(f"[Memory] {count} recuerdos cargados desde {self.persist_path}")

    def _build_faiss_index(self):
        """Construye o reconstruye el índice FAISS a partir de los vectores actuales."""
        if not HAS_FAISS or not self.vectors:
            return
        try:
            vecs = np.array(self.vectors, dtype=np.float32)
            dim = vecs.shape[1]
            self._embedding_dim = dim
            # Normalizar para similitud coseno vía Inner Product
            faiss.normalize_L2(vecs)
            index = faiss.IndexFlatIP(dim)
            index.add(vecs)
            self._faiss_index = index
        except Exception as e:
            print(f"[Memory] FAISS index build error: {e}")
            self._faiss_index = None

    def _load(self):
        """Carga persistencia desde JSON"""
        if os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    with self.lock:
                        self.documents = data.get('documents', [])
                        self.metadatas = data.get('metadatas', [])
                        self.vectors = data.get('vectors', [])
                # Reconstruir índice FAISS tras cargar
                if HAS_FAISS and self.vectors:
                    self._build_faiss_index()
            except Exception as e:
                print(f"[Error] Fallo al cargar memoria: {e}")

    def _save(self):
        """Guarda persistencia a JSON"""
        try:
            temp_path = self.persist_path + ".tmp"
            # Copia local para escritura segura
            with self.lock:
                data = {
                    'documents': self.documents,
                    'metadatas': self.metadatas,
                    'vectors': self.vectors
                }
            
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            # Operación atómica de renombrado
            os.replace(temp_path, self.persist_path)
        except Exception as e:
            print(f"[Error] Fallo al guardar memoria: {e}")

    def _faiss_add_single(self, vector: list):
        """Añade un solo vector al índice FAISS (llamar con lock ya adquirido)."""
        if not HAS_FAISS:
            return
        try:
            v = np.array([vector], dtype=np.float32)
            faiss.normalize_L2(v)
            dim = v.shape[1]
            if self._faiss_index is None or self._embedding_dim != dim:
                self._embedding_dim = dim
                self._faiss_index = faiss.IndexFlatIP(dim)
                # Reconstruir con todos los vectores existentes
                all_vecs = np.array(self.vectors, dtype=np.float32)
                faiss.normalize_L2(all_vecs)
                self._faiss_index.add(all_vecs)
            else:
                self._faiss_index.add(v)
        except Exception as e:
            print(f"[Memory] FAISS add error: {e}")

    def add_memory(self, text: str, metadata: Optional[Dict] = None, role: str = "user", memory_type: str = "longterm"):
        """Añade una memoria al almacén local"""
        if not text or len(text.strip()) < 5:
            return
            
        if metadata is None:
            metadata = {}
            
        # Guardar metadatos adicionales para compatibilidad
        metadata["role"] = role
        metadata["type"] = memory_type
        metadata.setdefault("timestamp", time.time())
        
        # Lanzar en hilo para no bloquear la respuesta
        thread = threading.Thread(target=self._insert_worker, args=(text, metadata))
        thread.daemon = True
        thread.start()

    def _insert_worker(self, text: str, metadata: Dict):
        """Worker para insertar en memoria"""
        from core.ollama_health import is_ollama_up, mark_ollama_down
        if not is_ollama_up():
            return  # Ollama down: skip embedding (avoids retry-spam in the bg thread)
        try:
            # Generar embedding
            vector = self.embedding_function.embed_query(text)
            
            with self.lock:
                # Almacenar
                self.documents.append(text)
                self.metadatas.append(metadata)
                self.vectors.append(vector)
                # Actualizar índice FAISS
                if HAS_FAISS:
                    self._faiss_add_single(vector)
                
                # Persistir
                self._save()
                
            print(f"[Memory] Nuevo recuerdo guardado.")
        except Exception as e:
            mark_ollama_down()
            print(f"[Error] Fallo al guardar en memoria: {e}")

    def search_relevant_context(self, query: str, k: int = 3) -> str:
        """Busca y devuelve el contexto relevante formateado como string.
        
        Usa FAISS si está disponible (búsqueda O(log n) vs O(n) con NumPy).
        """
        if not self.vectors:
            return ""

        # Circuit-breaker: la búsqueda corre ANTES de generar la respuesta (path crítico).
        # Si Ollama (embeddings) no responde, NO intentar — evitaba ~10-30s de reintentos por
        # turno. is_ollama_up() está cacheado (≤1 probe de 1s por ventana TTL).
        from core.ollama_health import is_ollama_up, mark_ollama_down
        if not is_ollama_up():
            return ""

        try:
            # Embedding de la consulta
            query_vector = self.embedding_function.embed_query(query)
            
            with self.lock:
                if not self.vectors:
                    return ""
                docs = list(self.documents)
                metas = list(self.metadatas)
                faiss_index = self._faiss_index
                vecs_snapshot = list(self.vectors)

            # ── Búsqueda con FAISS ────────────────────────────────────
            if HAS_FAISS and faiss_index is not None:
                q_vec = np.array([query_vector], dtype=np.float32)
                faiss.normalize_L2(q_vec)
                n_search = min(k, len(docs))
                scores, indices = faiss_index.search(q_vec, n_search)
                
                context_parts = []
                for score, idx in zip(scores[0], indices[0]):
                    if idx < 0 or idx >= len(docs):
                        continue
                    # FAISS IP sobre vectores normalizados == similitud coseno
                    if score > 0.65:
                        source = metas[idx].get("role", "unknown")
                        content = docs[idx].strip()
                        context_parts.append(f"- [{source}]: {content}")
                return "\n".join(context_parts)

            # ── Fallback: similitud coseno con NumPy ──────────────────
            vecs = np.array(vecs_snapshot)
            q_vec = np.array(query_vector)
            
            vecs_norm = np.linalg.norm(vecs, axis=1)
            q_norm = np.linalg.norm(q_vec)
            
            if q_norm == 0:
                return ""
            
            valid_indices = vecs_norm > 0
            if not np.any(valid_indices):
                return ""
                
            dot_products = np.dot(vecs[valid_indices], q_vec)
            similarities = dot_products / (vecs_norm[valid_indices] * q_norm)
            
            original_indices = np.where(valid_indices)[0]
            sorted_indices_local = np.argsort(similarities)[-k:][::-1]
            top_indices = original_indices[sorted_indices_local]
            
            context_parts = []
            for idx in top_indices:
                idx_in_valid = np.where(original_indices == idx)[0][0]
                score = similarities[idx_in_valid]
                # 0.6 (antes 0.4): el umbral bajo dejaba colar memorias de temas
                # distintos (p.ej. "noticias de IA" en una pregunta de biología),
                # contaminando el prompt. Alineado con el umbral FAISS (0.65).
                if score > 0.6:
                    source = metas[idx].get("role", "unknown")
                    content = docs[idx].strip()
                    context_parts.append(f"- [{source}]: {content}")
                
            return "\n".join(context_parts)
        except Exception as e:
            mark_ollama_down()
            print(f"[Error] Fallo al buscar en memoria vectorial: {e}")
            return ""
    
    def remove_last(self, n: int = 2):
        """Elimina las últimas N entradas (útil para borrar conversaciones recientes incorrectas)"""
        try:
            with self.lock:
                if n >= len(self.documents):
                    print(f"[Warning] Intentando borrar {n} entradas pero solo hay {len(self.documents)}")
                    n = len(self.documents)
                
                # Eliminar las últimas n entradas
                self.documents = self.documents[:-n] if n > 0 else self.documents
                self.metadatas = self.metadatas[:-n] if n > 0 else self.metadatas
                self.vectors = self.vectors[:-n] if n > 0 else self.vectors
                
                # Reconstruir índice FAISS tras eliminar
                if HAS_FAISS:
                    self._build_faiss_index()
                
                # Persistir cambios
                self._save()
                
            print(f"[Memory] {n} entradas eliminadas. Quedan {len(self.documents)} recuerdos.")
        except Exception as e:
            print(f"[Error] Fallo al eliminar entradas: {e}")
    
    def clear_all(self):
        """Limpia toda la memoria vectorial"""
        try:
            with self.lock:
                count = len(self.documents)
                self.documents = []
                self.metadatas = []
                self.vectors = []
                self._faiss_index = None
                self._save()

            print(f"[Memory] Memoria completamente limpiada ({count} entradas eliminadas).")
        except Exception as e:
            print(f"[Error] Fallo al limpiar memoria: {e}")

    def count(self) -> int:
        """Retorna número de memorias almacenadas."""
        with self.lock:
            return len(self.documents)

    def get_memory_count(self) -> int:
        return self.count()

    async def async_search_relevant_context(self, query: str, k: int = 3) -> str:
        """Async wrapper para search_relevant_context — evita bloquear el event loop."""
        import asyncio
        return await asyncio.to_thread(self.search_relevant_context, query, k)
