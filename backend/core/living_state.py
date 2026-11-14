"""
LivingState — Estado interno vivo de Zephyro.

Mantiene el estado de salud del sistema (skills, errores activos, cambios
recientes del codebase) de forma reactiva mediante el EventBus. Persiste
en SQLite (stdlib). Watchers de filesystem via watchdog (sin polling).

Requiere: pip install watchdog
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.event_bus import (
    AUTO_DIAGNOSE,
    GIT_COMMIT,
    ZEPHYRO_RESTART,
    LOG_ERROR,
    SKILL_EXECUTED,
    get_bus,
)

# ── watchdog (opcional) ───────────────────────────────────────────────────────
try:
    from watchdog.events import FileModifiedEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    Observer = None  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[assignment,misc]

# ── Log regex (mismo que DiagnosticAgent) ─────────────────────────────────────
_LOG_RE = re.compile(
    r"^(?P<ts>\d{2}:\d{2}:\d{2})\s+\[(?P<level>ERROR|WARN(?:ING)?)\]\s+"
    r"(?P<logger>[\w.]+):\s+(?P<message>.+)$"
)

_MAX_RECENT_CHANGES = 20
_DEGRADED_THRESHOLD = 2   # consecutive_failures >= 2 → degraded
_BROKEN_THRESHOLD = 3     # consecutive_failures >= 3 → broken
_EMA_ALPHA = 0.1           # suavizado exponencial para success_rate


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Watchdog handlers ──────────────────────────────────────────────────────────

class _GitWatchHandler(FileSystemEventHandler if HAS_WATCHDOG else object):
    """Detecta cambios en .git/refs/heads/ y dispara GIT_COMMIT."""

    def __init__(self, code_root: str, loop: asyncio.AbstractEventLoop) -> None:
        if HAS_WATCHDOG:
            super().__init__()
        self._code_root = code_root
        self._loop = loop
        self._log = logging.getLogger("zephyro.living_state.git_watcher")

    def on_modified(self, event: Any) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        self._loop.call_soon_threadsafe(
            lambda: self._loop.create_task(self._publish_commit())
        )

    async def _publish_commit(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", self._code_root, "log", "-1",
                "--format=%H|%s|%aI",
                "--name-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0 or not stdout:
                return
            lines = stdout.decode().strip().splitlines()
            if not lines:
                return
            parts = lines[0].split("|", 2)
            if len(parts) < 3:
                return
            commit_hash, message, timestamp = parts
            files_changed = [l.strip() for l in lines[1:] if l.strip()]
            get_bus().publish(GIT_COMMIT, {
                "commit_hash": commit_hash.strip(),
                "message": message.strip(),
                "timestamp": timestamp.strip(),
                "files_changed": files_changed,
            })
        except Exception as exc:
            self._log.warning("[git_watcher] Error al leer commit: %s", exc)


class _LogWatchHandler(FileSystemEventHandler if HAS_WATCHDOG else object):
    """Detecta nuevas líneas en el log del backend y dispara LOG_ERROR."""

    def __init__(
        self,
        log_file: str,
        loop: asyncio.AbstractEventLoop,
        offset_ref: list[int],
    ) -> None:
        if HAS_WATCHDOG:
            super().__init__()
        self._log_file = Path(log_file)
        self._loop = loop
        self._offset_ref = offset_ref  # mutable ref compartida con LivingState
        self._log = logging.getLogger("zephyro.living_state.log_watcher")

    def on_modified(self, event: Any) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        if Path(getattr(event, "src_path", "")).resolve() != self._log_file.resolve():
            return
        self._loop.call_soon_threadsafe(
            lambda: self._loop.create_task(self._read_new_lines())
        )

    async def _read_new_lines(self) -> None:
        try:
            if not self._log_file.exists():
                return
            size = self._log_file.stat().st_size
            if size <= self._offset_ref[0]:
                # Rotación de log — resetear offset
                if size < self._offset_ref[0]:
                    self._offset_ref[0] = 0
                return
            with self._log_file.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self._offset_ref[0])
                new_text = f.read()
                self._offset_ref[0] = f.tell()
            for line in new_text.splitlines():
                m = _LOG_RE.match(line.strip())
                if not m:
                    continue
                raw_level = m.group("level")
                level = "WARNING" if raw_level.startswith("WARN") else raw_level
                get_bus().publish(LOG_ERROR, {
                    "level": level,
                    "logger": m.group("logger"),
                    "message": m.group("message"),
                    "timestamp": m.group("ts"),
                    "raw": line,
                })
        except Exception as exc:
            self._log.warning("[log_watcher] Error al leer nuevas líneas: %s", exc)


# ── LivingState ───────────────────────────────────────────────────────────────

_MAX_RECENT_ERRORS = 50  # errores recientes en memoria para el panel Tauri


class LivingState:
    """
    Estado interno vivo de Zephyro. Reactivo a eventos del EventBus.
    Persiste en SQLite. Watchers filesystem via watchdog (sin polling).
    """

    def __init__(
        self,
        db_path: str,
        code_root: str,
        log_file: str,
    ) -> None:
        self._db_path = Path(db_path)
        self._code_root = str(Path(code_root).resolve())
        self._log_file = log_file
        self._log_offset: list[int] = [0]  # mutable ref para el log watcher
        self._tg: Any = None  # TelegramChannel inyectado post-creación
        self._observers: list[Any] = []
        self._state: dict[str, Any] = self._empty_state()
        self._recent_errors: list[dict[str, Any]] = []

        # Propio logger
        self._log = logging.getLogger("zephyro.living_state")
        log_dir = Path(__file__).parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        living_log = log_dir / "living_state.log"
        if not any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", None) == str(living_log.resolve())
            for h in self._log.handlers
        ):
            fh = logging.FileHandler(living_log, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            self._log.addHandler(fh)

    # ── Estado vacío ──────────────────────────────────────────────────────────

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "skills": {},
            "active_issues": [],
            "recent_changes": [],
            "current_version": "",
            "last_updated": _now_iso(),
            "system": {
                "uptime_since": _now_iso(),
                "restart_count": 0,
            },
        }

    # ── Persistencia SQLite ───────────────────────────────────────────────────

    def _db_conn(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS state_blob "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.commit()
        return conn

    def _load_state(self) -> None:
        try:
            conn = self._db_conn()
            row = conn.execute(
                "SELECT state_json FROM state_blob WHERE id = 1"
            ).fetchone()
            conn.close()
            if row:
                loaded = json.loads(row[0])
                # Merge con estado vacío para campos que puedan faltar en versiones antiguas
                empty = self._empty_state()
                empty.update(loaded)
                self._state = empty
                self._log.info("[living_state] Estado cargado desde DB")
            else:
                self._state = self._empty_state()
                self._log.info("[living_state] DB nueva — estado vacío inicializado")
        except Exception as exc:
            self._log.warning("[living_state] Error cargando estado: %s — usando vacío", exc)
            self._state = self._empty_state()

    def _save_state(self) -> None:
        self._state["last_updated"] = _now_iso()
        try:
            conn = self._db_conn()
            conn.execute(
                "INSERT OR REPLACE INTO state_blob (id, state_json, updated_at) VALUES (1, ?, ?)",
                (json.dumps(self._state), _now_iso()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            self._log.warning("[living_state] Error guardando estado: %s", exc)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Carga estado, suscribe handlers al bus, inicia watchers."""
        self._load_state()

        bus = get_bus()
        bus.subscribe(GIT_COMMIT, self._on_git_commit)
        bus.subscribe(LOG_ERROR, self._on_log_error)
        bus.subscribe(SKILL_EXECUTED, self._on_skill_executed)
        bus.subscribe(ZEPHYRO_RESTART, self._on_restart)

        self._start_watchers()

        # Publicar restart al iniciar
        get_bus().publish(ZEPHYRO_RESTART, {"timestamp": _now_iso()})
        self._log.info("LivingState iniciado (code_root=%s)", self._code_root)

    async def stop(self) -> None:
        """Detiene watchers y guarda estado."""
        for obs in self._observers:
            try:
                obs.stop()
                obs.join(timeout=3)
            except Exception:
                pass
        self._observers.clear()
        self._save_state()
        self._log.info("LivingState detenido")

    def _start_watchers(self) -> None:
        if not HAS_WATCHDOG:
            self._log.warning(
                "[living_state] watchdog no instalado — watchers desactivados. "
                "Instalar con: pip install watchdog"
            )
            return

        loop = asyncio.get_event_loop()

        # Git watcher
        git_refs_path = Path(self._code_root) / ".git" / "refs" / "heads"
        if git_refs_path.exists():
            git_handler = _GitWatchHandler(self._code_root, loop)
            git_obs = Observer()
            git_obs.schedule(git_handler, str(git_refs_path), recursive=True)
            git_obs.start()
            self._observers.append(git_obs)
            self._log.info("[living_state] Git watcher activo en %s", git_refs_path)
        else:
            self._log.warning("[living_state] .git/refs/heads no encontrado — git watcher desactivado")

        # Log watcher
        log_path = Path(self._log_file)
        if log_path.parent.exists():
            # Inicializar offset al tamaño actual para no releer histórico
            if log_path.exists():
                self._log_offset[0] = log_path.stat().st_size

            log_handler = _LogWatchHandler(self._log_file, loop, self._log_offset)
            log_obs = Observer()
            log_obs.schedule(log_handler, str(log_path.parent), recursive=False)
            log_obs.start()
            self._observers.append(log_obs)
            self._log.info("[living_state] Log watcher activo en %s", log_path.parent)
        else:
            self._log.warning("[living_state] Directorio de log no existe — log watcher desactivado")

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_git_commit(self, payload: dict[str, Any]) -> None:
        entry = {
            "commit_hash": payload.get("commit_hash", ""),
            "message": payload.get("message", ""),
            "timestamp": payload.get("timestamp", _now_iso()),
            "files_changed": payload.get("files_changed", []),
        }
        changes: list = self._state["recent_changes"]
        # Dedup por hash
        if any(c["commit_hash"] == entry["commit_hash"] for c in changes):
            return
        changes.insert(0, entry)
        if len(changes) > _MAX_RECENT_CHANGES:
            changes[:] = changes[:_MAX_RECENT_CHANGES]
        self._state["current_version"] = entry["commit_hash"]
        self._save_state()
        self._log.info("[living_state] Git commit registrado: %s", entry["commit_hash"][:8])

    async def _on_log_error(self, payload: dict[str, Any]) -> None:
        level = payload.get("level", "ERROR")
        logger_name = payload.get("logger", "unknown")
        message = payload.get("message", "")
        timestamp = payload.get("timestamp", _now_iso())
        raw = payload.get("raw", "")
        module = logger_name.split(".")[-1]
        dedup_key = f"{module}:{message[:60]}"

        issues: list = self._state["active_issues"]
        existing = next(
            (i for i in issues if f"{i['module']}:{i['error'][:60]}" == dedup_key
             and i["status"] != "resolved"),
            None,
        )

        is_new = existing is None
        if is_new:
            issue = {
                "id": str(uuid.uuid4()),
                "module": module,
                "error": message,
                "detected_at": _now_iso(),
                "status": "open",
            }
            issues.append(issue)
            self._log.info("[living_state] Nuevo issue en %s: %s", module, message[:80])

        # Actualizar skill_health si el logger apunta a una skill conocida
        if module in self._state["skills"]:
            sk = self._state["skills"][module]
            sk["consecutive_failures"] = sk.get("consecutive_failures", 0) + 1
            sk["status"] = self._compute_status(sk["consecutive_failures"])

        # Almacenar en el log de errores recientes (panel Tauri)
        error_entry = {
            "id": str(uuid.uuid4()),
            "timestamp": timestamp,
            "level": level,
            "module": module,
            "message": message,
            "raw": raw[:500] if raw else message[:500],
            "is_new": is_new,
        }
        self._recent_errors.insert(0, error_entry)
        if len(self._recent_errors) > _MAX_RECENT_ERRORS:
            self._recent_errors = self._recent_errors[:_MAX_RECENT_ERRORS]

        self._save_state()

        # Notificaciones Telegram (solo si TELEGRAM_ENABLED=true)
        if is_new and level == "ERROR" and self._telegram_enabled():
            await self._notify(f"🔴 Nuevo error detectado en [{module}]\n{message[:200]}")

        # Publicar AUTO_DIAGNOSE para que el listener en web_app.py lo gestione
        if is_new:
            get_bus().publish(AUTO_DIAGNOSE, {
                "module": module,
                "error": message,
                "level": level,
            })

    async def _on_skill_executed(self, payload: dict[str, Any]) -> None:
        skill_name: str = payload.get("skill_name", "unknown")
        success: bool = payload.get("success", True)

        skills: dict = self._state["skills"]
        if skill_name not in skills:
            skills[skill_name] = {
                "status": "ok",
                "last_run": _now_iso(),
                "success_rate": 1.0 if success else 0.0,
                "consecutive_failures": 0 if success else 1,
            }
        else:
            sk = skills[skill_name]
            sk["last_run"] = _now_iso()
            # EMA del success_rate
            prev_rate = sk.get("success_rate", 1.0)
            sk["success_rate"] = (
                (1 - _EMA_ALPHA) * prev_rate + _EMA_ALPHA * (1.0 if success else 0.0)
            )
            if success:
                sk["consecutive_failures"] = 0
            else:
                sk["consecutive_failures"] = sk.get("consecutive_failures", 0) + 1

            sk["status"] = self._compute_status(sk["consecutive_failures"])

            if sk["consecutive_failures"] >= _BROKEN_THRESHOLD and self._telegram_enabled():
                await self._notify(
                    f"⚠️ Skill [{skill_name}] ha fallado "
                    f"{sk['consecutive_failures']} veces consecutivas"
                )

        self._save_state()

    async def _on_restart(self, payload: dict[str, Any]) -> None:
        sys = self._state["system"]
        count = sys.get("restart_count", 0) + 1
        sys["restart_count"] = count
        sys["uptime_since"] = _now_iso()
        self._save_state()
        if count > 1 and self._telegram_enabled():
            await self._notify("🔄 Zephyro se reinició")
        self._log.info("[living_state] Restart #%d registrado", count)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_status(consecutive_failures: int) -> str:
        if consecutive_failures >= _BROKEN_THRESHOLD:
            return "broken"
        if consecutive_failures >= _DEGRADED_THRESHOLD:
            return "degraded"
        return "ok"

    async def _notify(self, text: str) -> None:
        """Envía notificación via Telegram si hay canal configurado."""
        if self._tg is None:
            return
        if not self._telegram_enabled():
            return
        try:
            authorized = getattr(self._tg, "_authorized_ids", set())
            if not authorized:
                return
            chat_id = next(iter(authorized))
            await self._tg._app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            self._log.warning("[living_state] Error enviando notificación: %s", exc)

    @staticmethod
    def _telegram_enabled() -> bool:
        """Verifica si las notificaciones Telegram están habilitadas."""
        val = os.environ.get("TELEGRAM_ENABLED", "true").lower()
        return val in ("1", "true", "yes", "on")

    # ── API pública ───────────────────────────────────────────────────────────

    def set_telegram_channel(self, channel: Any) -> None:
        """Inyecta el TelegramChannel para notificaciones."""
        self._tg = channel

    def get_error_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retorna los errores recientes para el panel Tauri."""
        return self._recent_errors[:limit]

    def get_skill_health(self, skill_name: str) -> dict[str, Any]:
        """Retorna el estado de salud de una skill específica."""
        return dict(self._state["skills"].get(skill_name, {
            "status": "unknown",
            "last_run": None,
            "success_rate": None,
            "consecutive_failures": 0,
        }))

    def get_active_issues(self) -> list[dict[str, Any]]:
        """Retorna issues no resueltos."""
        return [
            dict(i) for i in self._state["active_issues"]
            if i.get("status") != "resolved"
        ]

    def get_recent_changes(self, n: int = 5) -> list[dict[str, Any]]:
        """Retorna los N cambios más recientes del codebase."""
        return [dict(c) for c in self._state["recent_changes"][:n]]

    def mark_issue_resolved(self, issue_id: str) -> bool:
        """Marca un issue como resuelto. Retorna True si se encontró."""
        for issue in self._state["active_issues"]:
            if issue["id"] == issue_id:
                issue["status"] = "resolved"
                self._save_state()
                return True
        return False

    def snapshot_for_llm(self, tier: int = 1) -> str:
        """
        Genera un snapshot del estado para inyectar al LLM.

        tier=1: solo skills degraded/broken + issues abiertos (mínimo tokens)
        tier=2: todas las skills + métricas + recent_changes recientes
        tier=3: estado completo serializado
        """
        if tier >= 3:
            return json.dumps(self._state, indent=2, ensure_ascii=False)

        lines: list[str] = []
        system = self._state.get("system", {})
        lines.append(
            f"[Sistema] restart_count={system.get('restart_count', 0)} "
            f"uptime_since={system.get('uptime_since', 'N/A')}"
        )

        # Skills
        skills = self._state.get("skills", {})
        if tier == 1:
            degraded = {k: v for k, v in skills.items() if v.get("status") != "ok"}
        else:
            degraded = skills

        if degraded:
            lines.append("\n[Skills]")
            for name, sk in degraded.items():
                rate = sk.get("success_rate")
                rate_str = f"{rate:.1%}" if rate is not None else "N/A"
                lines.append(
                    f"  {name}: status={sk.get('status','?')} "
                    f"success_rate={rate_str} "
                    f"fails={sk.get('consecutive_failures', 0)} "
                    f"last_run={sk.get('last_run', 'N/A')}"
                )

        # Issues
        open_issues = self.get_active_issues()
        if open_issues:
            lines.append(f"\n[Issues abiertos] ({len(open_issues)})")
            for iss in open_issues:
                lines.append(
                    f"  [{iss.get('module','?')}] {iss.get('error','')[:120]} "
                    f"({iss.get('detected_at','?')}) id={iss.get('id','?')[:8]}"
                )

        # Recent changes (tier >= 2)
        if tier >= 2:
            changes = self.get_recent_changes(3)
            if changes:
                lines.append("\n[Cambios recientes]")
                for ch in changes:
                    files = ", ".join(ch.get("files_changed", [])[:5])
                    lines.append(
                        f"  {ch.get('commit_hash','?')[:8]} — {ch.get('message','?')} "
                        f"({ch.get('timestamp','?')}) files=[{files}]"
                    )

        if len(lines) <= 1:
            lines.append("Sin issues ni skills degradadas.")

        return "\n".join(lines)
