"""
DiagnosticStore — JSON persistence for in-flight diagnostics.

Each diagnostic lives in `storage/diagnostics/{request_id}.json` so the Tauri
client can resume state after disconnect/reload, and expired entries are
cleaned up on startup.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("zephyro.diagnostic.store")

DIAGNOSTICS_DIR = Path("storage/diagnostics")


class DiagnosticStore:
    """Filesystem-backed store for DiagnosticAgent state."""

    def __init__(self, base_dir: Path = DIAGNOSTICS_DIR) -> None:
        self._dir = base_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, request_id: str) -> Path:
        # Prevent path traversal — request_ids are always short uuid prefixes.
        safe = "".join(c for c in request_id if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError(f"Invalid request_id: {request_id!r}")
        return self._dir / f"{safe}.json"

    def save(self, request_id: str, state: dict[str, Any]) -> None:
        path = self._path(request_id)
        try:
            state.setdefault("request_id", request_id)
            state["updated_at"] = time.time()
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.error("Failed to save diagnostic %s: %s", request_id, exc)

    def load(self, request_id: str) -> Optional[dict[str, Any]]:
        path = self._path(request_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Failed to load diagnostic %s: %s", request_id, exc)
            return None

    def update(self, request_id: str, **fields: Any) -> None:
        state = self.load(request_id) or {"request_id": request_id}
        state.update(fields)
        self.save(request_id, state)

    def append_progress(self, request_id: str, text: str) -> None:
        state = self.load(request_id) or {"request_id": request_id, "progress_log": []}
        state.setdefault("progress_log", []).append({"ts": time.time(), "text": text})
        self.save(request_id, state)

    def list_pending(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("status") not in ("running", "awaiting_approval"):
                continue
            if user_id is not None and str(data.get("user_id")) != str(user_id):
                continue
            out.append(data)
        return out

    def delete(self, request_id: str) -> None:
        path = self._path(request_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Failed to delete diagnostic %s: %s", request_id, exc)

    def cleanup_expired(self, max_age_hours: float = 1.0) -> int:
        """Remove JSON files older than max_age_hours. Returns count removed."""
        cutoff = time.time() - (max_age_hours * 3600.0)
        removed = 0
        for path in self._dir.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        if removed:
            log.info("Cleaned up %d expired diagnostic(s)", removed)
        return removed
