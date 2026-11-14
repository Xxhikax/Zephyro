"""
SessionWorkspace: Espacios de trabajo persistentes entre sesiones.

Permite a Zephyro recordar el contexto de proyectos específicos:
qué archivos se estaban editando, qué tareas estaban en progreso,
notas y decisiones tomadas en conversaciones anteriores.
"""
import sqlite3
import json
import time
import threading
import os
from typing import Dict, List, Optional, Any


DB_PATH = os.path.join(
    os.path.dirname(__file__), "../../../storage/workspaces.db"
)


class SessionWorkspace:
    """
    Espacios de trabajo persistentes entre sesiones.

    Un workspace tiene:
    - Nombre identificativo (ej: "proyecto-web", "tareas-diarias")
    - Archivos activos (rutas que el usuario está trabajando)
    - Notas libres
    - Tareas pendientes
    - Historial de decisiones
    - Contexto de la última sesión
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._active_workspace: Optional[str] = None
        self._init_db()
        print("[SessionWorkspace] Sistema de workspaces activo.")

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS workspaces (
                        name TEXT PRIMARY KEY,
                        description TEXT DEFAULT '',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        last_context TEXT DEFAULT ''
                    );

                    CREATE TABLE IF NOT EXISTS workspace_files (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        added_at REAL NOT NULL,
                        UNIQUE(workspace, file_path),
                        FOREIGN KEY(workspace) REFERENCES workspaces(name) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS workspace_notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace TEXT NOT NULL,
                        note TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        FOREIGN KEY(workspace) REFERENCES workspaces(name) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS workspace_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace TEXT NOT NULL,
                        task TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at REAL NOT NULL,
                        completed_at REAL,
                        FOREIGN KEY(workspace) REFERENCES workspaces(name) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS workspace_decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        FOREIGN KEY(workspace) REFERENCES workspaces(name) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS active_workspace (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        name TEXT,
                        updated_at REAL NOT NULL
                    );
                """)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Gestión de workspaces
    # ------------------------------------------------------------------

    def create_workspace(self, name: str, description: str = "") -> bool:
        """Crea un nuevo workspace."""
        name = name.strip().lower().replace(" ", "-")
        if not name:
            return False
        with self._lock:
            conn = self._get_conn()
            try:
                now = time.time()
                conn.execute(
                    "INSERT OR IGNORE INTO workspaces (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (name, description, now, now)
                )
                conn.commit()
                return True
            except Exception as e:
                print(f"[Workspace] Error creando workspace '{name}': {e}")
                return False
            finally:
                conn.close()

    def switch_workspace(self, name: str) -> bool:
        """Cambia al workspace especificado (lo crea si no existe)."""
        name = name.strip().lower().replace(" ", "-")
        self.create_workspace(name)
        self._active_workspace = name
        print(f"[Workspace] Workspace activo: {name}")
        return True

    def get_active_workspace(self) -> Optional[str]:
        """Devuelve el nombre del workspace activo."""
        return self._active_workspace

    def get_active(self) -> Optional[str]:
        """Recupera el active_workspace persistido en SQLite (singleton)."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT name FROM active_workspace WHERE id = 1"
                ).fetchone()
                return row["name"] if row and row["name"] else None
            finally:
                conn.close()

    def set_active(self, name: Optional[str]) -> bool:
        """Persiste el active_workspace. Pasar None lo limpia."""
        name = (name or "").strip().lower().replace(" ", "-") or None
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO active_workspace (id, name, updated_at) VALUES (1, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
                    (name, time.time())
                )
                conn.commit()
                self._active_workspace = name
                return True
            except Exception as e:
                print(f"[Workspace] Error setting active workspace: {e}")
                return False
            finally:
                conn.close()

    def list_workspaces(self) -> List[Dict]:
        """Lista todos los workspaces."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT name, description, created_at, updated_at FROM workspaces ORDER BY updated_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def delete_workspace(self, name: str) -> bool:
        """Elimina un workspace y todos sus datos."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM workspaces WHERE name = ?", (name,))
                conn.commit()
                if self._active_workspace == name:
                    self._active_workspace = None
                return True
            except Exception as e:
                print(f"[Workspace] Error eliminando '{name}': {e}")
                return False
            finally:
                conn.close()

    def _touch_workspace(self, name: str, conn: sqlite3.Connection):
        """Actualiza el timestamp del workspace."""
        conn.execute(
            "UPDATE workspaces SET updated_at = ? WHERE name = ?",
            (time.time(), name)
        )

    # ------------------------------------------------------------------
    # Archivos activos
    # ------------------------------------------------------------------

    def add_file(self, file_path: str, workspace: Optional[str] = None) -> bool:
        """Añade un archivo al workspace activo."""
        ws = workspace or self._active_workspace
        if not ws:
            return False
        self.create_workspace(ws)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO workspace_files (workspace, file_path, added_at) VALUES (?, ?, ?)",
                    (ws, file_path, time.time())
                )
                self._touch_workspace(ws, conn)
                conn.commit()
                return True
            except Exception as e:
                print(f"[Workspace] Error añadiendo archivo: {e}")
                return False
            finally:
                conn.close()

    def remove_file(self, file_path: str, workspace: Optional[str] = None) -> bool:
        """Elimina un archivo del workspace."""
        ws = workspace or self._active_workspace
        if not ws:
            return False
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "DELETE FROM workspace_files WHERE workspace = ? AND file_path = ?",
                    (ws, file_path)
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def get_files(self, workspace: Optional[str] = None) -> List[str]:
        """Lista los archivos del workspace."""
        ws = workspace or self._active_workspace
        if not ws:
            return []
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT file_path FROM workspace_files WHERE workspace = ? ORDER BY added_at DESC",
                    (ws,)
                ).fetchall()
                return [r["file_path"] for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Notas
    # ------------------------------------------------------------------

    def add_note(self, note: str, workspace: Optional[str] = None) -> bool:
        """Añade una nota al workspace."""
        ws = workspace or self._active_workspace
        if not ws or not note.strip():
            return False
        self.create_workspace(ws)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO workspace_notes (workspace, note, created_at) VALUES (?, ?, ?)",
                    (ws, note.strip(), time.time())
                )
                self._touch_workspace(ws, conn)
                conn.commit()
                return True
            finally:
                conn.close()

    def get_notes(self, workspace: Optional[str] = None, limit: int = 10) -> List[Dict]:
        """Obtiene las notas del workspace."""
        ws = workspace or self._active_workspace
        if not ws:
            return []
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT id, note, created_at FROM workspace_notes WHERE workspace = ? ORDER BY created_at DESC LIMIT ?",
                    (ws, limit)
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Tareas
    # ------------------------------------------------------------------

    def add_task(self, task: str, workspace: Optional[str] = None) -> Optional[int]:
        """Añade una tarea al workspace. Devuelve el ID de la tarea."""
        ws = workspace or self._active_workspace
        if not ws or not task.strip():
            return None
        self.create_workspace(ws)
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "INSERT INTO workspace_tasks (workspace, task, status, created_at) VALUES (?, ?, 'pending', ?)",
                    (ws, task.strip(), time.time())
                )
                self._touch_workspace(ws, conn)
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()

    def complete_task(self, task_id: int) -> bool:
        """Marca una tarea como completada."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE workspace_tasks SET status = 'done', completed_at = ? WHERE id = ?",
                    (time.time(), task_id)
                )
                conn.commit()
                return conn.execute(
                    "SELECT changes()"
                ).fetchone()[0] > 0
            finally:
                conn.close()

    def get_tasks(self, workspace: Optional[str] = None, status: str = "pending") -> List[Dict]:
        """Obtiene las tareas del workspace."""
        ws = workspace or self._active_workspace
        if not ws:
            return []
        with self._lock:
            conn = self._get_conn()
            try:
                if status == "all":
                    rows = conn.execute(
                        "SELECT id, task, status, created_at, completed_at FROM workspace_tasks WHERE workspace = ? ORDER BY created_at DESC",
                        (ws,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, task, status, created_at, completed_at FROM workspace_tasks WHERE workspace = ? AND status = ? ORDER BY created_at DESC",
                        (ws, status)
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Contexto de última sesión
    # ------------------------------------------------------------------

    def save_session_context(self, context: str, workspace: Optional[str] = None):
        """Guarda el contexto de la sesión actual para recuperarlo luego."""
        ws = workspace or self._active_workspace
        if not ws:
            return
        self.create_workspace(ws)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE workspaces SET last_context = ?, updated_at = ? WHERE name = ?",
                    (context[:2000], time.time(), ws)
                )
                conn.commit()
            finally:
                conn.close()

    def get_session_context(self, workspace: Optional[str] = None) -> str:
        """Recupera el contexto de la última sesión."""
        ws = workspace or self._active_workspace
        if not ws:
            return ""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT last_context FROM workspaces WHERE name = ?",
                    (ws,)
                ).fetchone()
                return row["last_context"] if row else ""
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Resumen para el LLM
    # ------------------------------------------------------------------

    def get_context_for_llm(self, workspace: Optional[str] = None) -> str:
        """Devuelve un resumen del workspace activo para incluir en el contexto."""
        ws = workspace or self._active_workspace
        if not ws:
            return ""

        files = self.get_files(ws)
        tasks = self.get_tasks(ws, status="pending")
        notes = self.get_notes(ws, limit=3)
        last_ctx = self.get_session_context(ws)

        parts = [f"## Workspace activo: {ws}"]

        if files:
            parts.append(f"Archivos activos: {', '.join(files[:5])}")

        if tasks:
            task_list = "; ".join(t["task"] for t in tasks[:5])
            parts.append(f"Tareas pendientes: {task_list}")

        if notes:
            note_list = " | ".join(n["note"][:100] for n in notes[:3])
            parts.append(f"Notas recientes: {note_list}")

        if last_ctx:
            parts.append(f"Última sesión: {last_ctx[:300]}")

        return "\n".join(parts)

    def get_full_workspace(self, workspace: Optional[str] = None) -> Dict[str, Any]:
        """Devuelve el workspace completo como dict."""
        ws = workspace or self._active_workspace
        if not ws:
            return {}

        return {
            "name": ws,
            "files": self.get_files(ws),
            "notes": self.get_notes(ws),
            "pending_tasks": self.get_tasks(ws, "pending"),
            "done_tasks": self.get_tasks(ws, "done"),
            "last_context": self.get_session_context(ws),
        }
