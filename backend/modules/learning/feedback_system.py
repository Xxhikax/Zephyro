"""
FeedbackSystem — Captura señales de calidad de respuesta para aprendizaje adaptativo.

Señales explícitas: thumbs up/down via API endpoint.
Señales implícitas: correcciones ("no, lo que quería decir..."), repeticiones.
"""
import sqlite3
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

_CORRECTION_PATTERNS = [
    "no, lo que quería", "no, quería decir", "me refería a",
    "no era eso", "eso no es lo que", "no es correcto", "está mal",
    "equivocado", "no entendiste", "no te pregunté eso",
    "no era lo que", "lo que dije fue",
]

_REPEAT_WINDOW_SECONDS = 120  # Considera repetición si la misma pregunta ocurre en <2 min


class FeedbackSystem:
    DB_PATH = Path(__file__).parent.parent.parent / "storage" / "feedback.db"

    def __init__(self):
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp        REAL NOT NULL,
                    session_id       TEXT DEFAULT '',
                    user_input       TEXT NOT NULL,
                    response         TEXT NOT NULL,
                    intent           TEXT DEFAULT 'chat',
                    importance_score REAL DEFAULT 0.0,
                    feedback_score   INTEGER DEFAULT 0,
                    feedback_type    TEXT DEFAULT 'none',
                    exported         INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_score     ON interactions(feedback_score);
                CREATE INDEX IF NOT EXISTS idx_exported  ON interactions(exported);
                CREATE INDEX IF NOT EXISTS idx_session   ON interactions(session_id);
            """)

    def record_interaction(
        self,
        user_input: str,
        response: str,
        intent: str = "chat",
        importance_score: float = 0.0,
        session_id: str = "",
    ) -> int:
        """Registra una interacción y devuelve su ID para correlacionar feedback posterior."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO interactions
                   (timestamp, session_id, user_input, response, intent, importance_score)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (time.time(), session_id, user_input[:2000], response[:4000], intent, importance_score),
            )
            return cur.lastrowid

    def submit_feedback(self, interaction_id: int, score: int, feedback_type: str = "explicit") -> bool:
        """
        Registra feedback: score=1 (bueno), score=-1 (malo), score=0 (neutro).
        Devuelve True si la interacción fue encontrada y actualizada.
        """
        if score not in (-1, 0, 1):
            return False
        with self._conn() as conn:
            n = conn.execute(
                "UPDATE interactions SET feedback_score=?, feedback_type=? WHERE id=?",
                (score, feedback_type, interaction_id),
            ).rowcount
        return n > 0

    def detect_implicit_feedback(
        self,
        user_input: str,
        last_interaction_id: Optional[int],
        last_user_input: Optional[str] = None,
    ) -> Optional[int]:
        """
        Detecta señales implícitas en el nuevo input del usuario.
        Si detecta corrección o repetición, actualiza la interacción anterior.
        Devuelve el score inferido (-1) o None si no hay señal.
        """
        if not last_interaction_id:
            return None

        lower = user_input.lower()

        # Corrección explícita → negativa
        if any(p in lower for p in _CORRECTION_PATTERNS):
            self.submit_feedback(last_interaction_id, -1, "correction")
            return -1

        # Repetición de la misma pregunta en poco tiempo → negativa
        if last_user_input and len(user_input) > 10:
            if lower.strip() == last_user_input.lower().strip() or \
               user_input[:25].lower() == last_user_input[:25].lower():
                with self._conn() as conn:
                    row = conn.execute(
                        "SELECT timestamp FROM interactions WHERE id=?",
                        (last_interaction_id,),
                    ).fetchone()
                if row and (time.time() - row["timestamp"]) < _REPEAT_WINDOW_SECONDS:
                    self.submit_feedback(last_interaction_id, -1, "repeat")
                    return -1

        return None

    def get_good_interactions(
        self,
        min_score: int = 1,
        not_exported: bool = False,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Devuelve interacciones con feedback positivo para entrenamiento y few-shot."""
        query = """
            SELECT id, user_input, response, intent, importance_score, timestamp
            FROM interactions
            WHERE feedback_score >= ?
        """
        params: list = [min_score]
        if not_exported:
            query += " AND exported = 0"
        query += " ORDER BY importance_score DESC, timestamp DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "id": r["id"],
                "user_input": r["user_input"],
                "response": r["response"],
                "intent": r["intent"],
                "importance_score": r["importance_score"],
                "timestamp": r["timestamp"],
            }
            for r in rows
        ]

    def mark_exported(self, interaction_ids: List[int]):
        """Marca interacciones como ya incluidas en un ciclo de entrenamiento."""
        if not interaction_ids:
            return
        placeholders = ",".join("?" * len(interaction_ids))
        with self._conn() as conn:
            conn.execute(
                f"UPDATE interactions SET exported=1 WHERE id IN ({placeholders})",
                interaction_ids,
            )

    def get_stats(self) -> Dict[str, Any]:
        with self._conn() as conn:
            total    = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
            positive = conn.execute("SELECT COUNT(*) FROM interactions WHERE feedback_score=1").fetchone()[0]
            negative = conn.execute("SELECT COUNT(*) FROM interactions WHERE feedback_score=-1").fetchone()[0]
            exported = conn.execute("SELECT COUNT(*) FROM interactions WHERE exported=1").fetchone()[0]
        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "exported": exported,
            "pending_training": max(0, positive - exported),
        }
