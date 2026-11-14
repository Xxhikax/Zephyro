"""
UserProfile: Perfil dinámico del usuario con aprendizaje continuo.

Aprende preferencias, intereses, nombre y patrones de uso
a lo largo de las conversaciones. Persiste en SQLite.
"""
import sqlite3
import json
import time
import threading
import os
from typing import Dict, List, Optional, Any
from collections import Counter


DB_PATH = os.path.join(os.path.dirname(__file__), "../../../storage/user_profile.db")


class UserProfile:
    """
    Perfil dinámico del usuario.
    
    Almacena y actualiza automáticamente:
    - Nombre y datos básicos
    - Temas de interés (con frecuencia)
    - Preferencias de idioma, formato, verbosidad
    - Historial de actividades recientes
    - Patrones de horario
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        print("[UserProfile] Perfil de usuario activo.")

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS profile (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS interests (
                        topic TEXT PRIMARY KEY,
                        count INTEGER NOT NULL DEFAULT 1,
                        last_seen REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS activity_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        activity TEXT NOT NULL,
                        timestamp REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS preferences (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    );
                """)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Lectura / escritura de datos básicos
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Obtiene un dato del perfil."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT value FROM profile WHERE key = ?", (key,)
                ).fetchone()
                if row:
                    try:
                        return json.loads(row["value"])
                    except (json.JSONDecodeError, TypeError):
                        return row["value"]
                return default
            finally:
                conn.close()

    def set(self, key: str, value: Any):
        """Establece un dato del perfil."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO profile (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value), time.time())
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Intereses / temas
    # ------------------------------------------------------------------

    def add_interest(self, topic: str):
        """Registra o incrementa un tema de interés."""
        topic = topic.strip().lower()
        if not topic or len(topic) < 3:
            return
        with self._lock:
            conn = self._get_conn()
            try:
                existing = conn.execute(
                    "SELECT count FROM interests WHERE topic = ?", (topic,)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE interests SET count = count + 1, last_seen = ? WHERE topic = ?",
                        (time.time(), topic)
                    )
                else:
                    conn.execute(
                        "INSERT INTO interests (topic, count, last_seen) VALUES (?, 1, ?)",
                        (topic, time.time())
                    )
                conn.commit()
            finally:
                conn.close()

    def get_top_interests(self, n: int = 10) -> List[Dict]:
        """Devuelve los N temas más frecuentes."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT topic, count, last_seen FROM interests ORDER BY count DESC LIMIT ?",
                    (n,)
                ).fetchall()
                return [{"topic": r["topic"], "count": r["count"], "last_seen": r["last_seen"]}
                        for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Preferencias
    # ------------------------------------------------------------------

    def set_preference(self, key: str, value: Any):
        """Guarda una preferencia del usuario."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO preferences (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value), time.time())
                )
                conn.commit()
            finally:
                conn.close()

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Lee una preferencia."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT value FROM preferences WHERE key = ?", (key,)
                ).fetchone()
                if row:
                    try:
                        return json.loads(row["value"])
                    except (json.JSONDecodeError, TypeError):
                        return row["value"]
                return default
            finally:
                conn.close()

    def get_all_preferences(self) -> Dict[str, Any]:
        """Devuelve todas las preferencias."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("SELECT key, value FROM preferences").fetchall()
                result = {}
                for r in rows:
                    try:
                        result[r["key"]] = json.loads(r["value"])
                    except (json.JSONDecodeError, TypeError):
                        result[r["key"]] = r["value"]
                return result
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Log de actividad
    # ------------------------------------------------------------------

    def log_activity(self, activity: str):
        """Registra una actividad reciente."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO activity_log (activity, timestamp) VALUES (?, ?)",
                    (activity, time.time())
                )
                # Mantener solo los últimos 500 registros
                conn.execute("""
                    DELETE FROM activity_log WHERE id NOT IN (
                        SELECT id FROM activity_log ORDER BY timestamp DESC LIMIT 500
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    def get_recent_activities(self, n: int = 20) -> List[Dict]:
        """Devuelve las N actividades más recientes."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT activity, timestamp FROM activity_log ORDER BY timestamp DESC LIMIT ?",
                    (n,)
                ).fetchall()
                return [{"activity": r["activity"], "timestamp": r["timestamp"]} for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Análisis automático de mensajes
    # ------------------------------------------------------------------

    # Palabras que indican temas de interés
    _TOPIC_KEYWORDS = {
        "programación": ["python", "código", "función", "clase", "algoritmo", "bug", "error", "script"],
        "música": ["música", "canción", "spotify", "youtube music", "reproducir", "playlist"],
        "finanzas": ["bitcoin", "crypto", "precio", "mercado", "inversión", "euro", "dólar"],
        "noticias": ["noticia", "titular", "rss", "periódico", "artículo"],
        "sistema": ["archivo", "carpeta", "git", "terminal", "bash", "linux"],
        "ia": ["ia", "inteligencia artificial", "modelo", "llm", "gpt", "claude"],
    }

    def learn_from_message(self, message: str):
        """
        Aprende automáticamente del mensaje del usuario:
        - Detecta y registra temas de interés
        - Detecta nombre si se presenta
        - Detecta preferencias de idioma/formato
        """
        if not message:
            return

        msg_lower = message.lower()

        # Detectar nombre
        for phrase in ["me llamo ", "soy ", "mi nombre es "]:
            if phrase in msg_lower:
                idx = msg_lower.find(phrase) + len(phrase)
                words = msg_lower[idx:].split()
                if words:
                    name = words[0].strip(".,!?").capitalize()
                    if 2 < len(name) < 30 and name.isalpha():
                        current_name = self.get("name")
                        if not current_name or current_name.lower() != name.lower():
                            self.set("name", name)

        # Detectar temas de interés
        for topic, keywords in self._TOPIC_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                self.add_interest(topic)

        # Detectar preferencia de idioma (si escribe en otro idioma)
        # (básico: si el mensaje tiene muchas palabras en inglés)
        english_words = {"the", "is", "are", "was", "were", "have", "has", "do", "does", "can", "will"}
        words_in_msg = set(msg_lower.split())
        if len(words_in_msg & english_words) >= 3:
            self.set_preference("language", "en")
        else:
            self.set_preference("language", "es")

    # ------------------------------------------------------------------
    # Resumen del perfil
    # ------------------------------------------------------------------

    def get_summary(self) -> str:
        """Devuelve un resumen del perfil para incluir en el contexto del LLM."""
        name = self.get("name")
        interests = self.get_top_interests(5)
        prefs = self.get_all_preferences()

        parts = []
        if name:
            parts.append(f"El usuario se llama {name}.")

        if interests:
            topics = ", ".join(i["topic"] for i in interests)
            parts.append(f"Temas de interés: {topics}.")

        lang = prefs.get("language", "es")
        if lang == "en":
            parts.append("El usuario prefiere comunicarse en inglés.")

        return " ".join(parts) if parts else ""

    def get_full_profile(self) -> Dict:
        """Devuelve el perfil completo como dict."""
        return {
            "name": self.get("name"),
            "interests": self.get_top_interests(10),
            "preferences": self.get_all_preferences(),
            "recent_activities": self.get_recent_activities(10),
        }
