# core/llm_cache.py
"""
Cache inteligente para respuestas LLM.
- LRU en memoria (sin dependencias externas)
- Hash SHA256 de prompts para claves
- TTL configurable por tipo de consulta
- Estadísticas de hit/miss
- Invalidación manual
"""
import hashlib
import time
from collections import OrderedDict
from typing import Optional, Tuple
import threading


class LLMCache:
    """
    Cache LRU thread-safe para respuestas de LLMs.

    Estrategia de TTL:
    - Saludos / triviales  → 3600s  (1 hora)
    - Info general (wiki, conceptos) → 1800s (30 min)
    - Datos en tiempo real (clima, crypto) → 300s  (5 min)
    - Default                → 900s   (15 min)
    """

    # Palabras clave que indican datos en tiempo real (TTL corto: 5 min)
    _REALTIME_KEYWORDS = frozenset([
        "precio", "cotización", "cotizacion", "tiempo", "clima", "temperatura",
        "noticias", "news", "bitcoin", "ethereum", "crypto", "bolsa", "acciones",
        "ahora", "hoy", "actual", "live", "real-time",
        # Eventos políticos / personas en cargo (cambian con el tiempo)
        "presidente", "presidenta", "primer ministro", "gobierno", "gobernador",
        "elecciones", "elección", "eleccion", "quien es el", "quién es el",
        "quien gobierna", "quién gobierna", "situacion actual", "situación actual",
        "últimas noticias", "ultimas noticias", "noticias recientes",
    ])

    # Palabras clave para saludos (TTL muy largo)
    _GREETING_KEYWORDS = frozenset([
        "hola", "buenos días", "buenas tardes", "buenas noches", "hey", "gracias"
    ])

    def __init__(self, max_size: int = 500):
        self._cache: OrderedDict[str, Tuple[str, float, float]] = OrderedDict()
        # key → (response, expires_at, created_at)
        self._max_size = max_size
        self._lock = threading.Lock()

        # Estadísticas
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_prompt(prompt: str) -> str:
        """SHA256 del prompt (normalizado)."""
        normalized = " ".join(prompt.lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _get_ttl(self, prompt: str) -> float:
        """Determina el TTL en segundos según el contenido del prompt."""
        prompt_lower = prompt.lower()

        if any(kw in prompt_lower for kw in self._GREETING_KEYWORDS):
            return 3600.0  # 1 hora

        if any(kw in prompt_lower for kw in self._REALTIME_KEYWORDS):
            return 300.0  # 5 minutos

        # Prompt largo → probablemente respuesta de análisis → 30 min
        if len(prompt) > 500:
            return 1800.0

        return 900.0  # 15 min por defecto

    # ------------------------------------------------------------------
    # Operaciones principales
    # ------------------------------------------------------------------

    def get(self, prompt: str) -> Optional[str]:
        """Retorna la respuesta cacheada o None si no existe / expirada."""
        key = self._hash_prompt(prompt)
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            response, expires_at, _ = self._cache[key]

            # Verificar expiración
            if time.time() > expires_at:
                del self._cache[key]
                self._misses += 1
                return None

            # LRU: mover al final
            self._cache.move_to_end(key)
            self._hits += 1
            return response

    def set(self, prompt: str, response: str) -> None:
        """Almacena una respuesta en cache con TTL automático."""
        if not prompt or not response:
            return

        key = self._hash_prompt(prompt)
        ttl = self._get_ttl(prompt)
        expires_at = time.time() + ttl

        with self._lock:
            if key in self._cache:
                # Actualizar existente y mover al final
                self._cache[key] = (response, expires_at, time.time())
                self._cache.move_to_end(key)
                return

            # Evicción LRU si estamos al límite
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
                self._evictions += 1

            self._cache[key] = (response, expires_at, time.time())

    def invalidate(self, prompt: str) -> bool:
        """Invalida una entrada específica. Retorna True si existía."""
        key = self._hash_prompt(prompt)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> int:
        """Limpia toda la cache. Retorna el número de entradas eliminadas."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def purge_expired(self) -> int:
        """Elimina entradas expiradas. Retorna cuántas se eliminaron."""
        now = time.time()
        with self._lock:
            expired_keys = [
                k for k, (_, expires_at, _) in self._cache.items()
                if now > expires_at
            ]
            for k in expired_keys:
                del self._cache[k]
            return len(expired_keys)

    # ------------------------------------------------------------------
    # Estadísticas
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict:
        """Retorna estadísticas del cache."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0.0
            now = time.time()
            active = sum(
                1 for _, (_, expires_at, _) in self._cache.items()
                if now <= expires_at
            )
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate_pct": round(hit_rate, 1),
                "total_entries": len(self._cache),
                "active_entries": active,
                "max_size": self._max_size,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def __repr__(self) -> str:
        s = self.stats
        return (
            f"LLMCache(size={s['total_entries']}/{s['max_size']}, "
            f"hit_rate={s['hit_rate_pct']}%, "
            f"hits={s['hits']}, misses={s['misses']})"
        )
