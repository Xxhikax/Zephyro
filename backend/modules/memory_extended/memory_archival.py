"""
memory_archival — separación factual/procedimental + cold storage.

- Factual:        vector_store + user_profile + summaries (queda hot)
- Procedimental:  skills/community/ (los SKILL.md aprobados)
- Cold archival:  sesiones JSON > 90 días → comprimidas en sessions_archive/

Programado por scheduler (cron 3am diario). Reusa conversation_summarizer
para generar resumen pre-archival si está disponible.
"""
from __future__ import annotations

import gzip
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("zephyro.memory_archival")


DEFAULT_SESSIONS_DIR = Path("storage/sessions")
DEFAULT_ARCHIVE_DIR = Path("storage/sessions_archive")
DEFAULT_MAX_AGE_DAYS = 90.0


class MemoryArchival:

    def __init__(
        self,
        sessions_dir: Optional[Path] = None,
        archive_dir: Optional[Path] = None,
        max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    ) -> None:
        self.sessions_dir = sessions_dir or DEFAULT_SESSIONS_DIR
        self.archive_dir = archive_dir or DEFAULT_ARCHIVE_DIR
        self.max_age_days = max_age_days
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    async def move_cold(self) -> dict[str, Any]:
        """
        Mueve sesiones más viejas que `max_age_days` al archive,
        comprimidas con gzip. Devuelve un dict con métricas.
        """
        if not self.sessions_dir.exists():
            return {"moved": 0, "errors": 0, "reason": "no sessions_dir"}

        cutoff = time.time() - (self.max_age_days * 86400.0)
        moved = 0
        errors = 0
        total_bytes = 0

        for path in self.sessions_dir.iterdir():
            if not path.is_file() or path.suffix != ".json":
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                size = path.stat().st_size
                if self._archive_one(path):
                    moved += 1
                    total_bytes += size
                else:
                    errors += 1
            except OSError as exc:
                log.warning("[archival] error procesando %s: %s", path, exc)
                errors += 1

        log.info("[archival] %d sesiones movidas (%d bytes), %d errores",
                 moved, total_bytes, errors)
        return {
            "moved": moved,
            "errors": errors,
            "bytes_archived": total_bytes,
            "cutoff_days": self.max_age_days,
        }

    def _archive_one(self, path: Path) -> bool:
        target = self.archive_dir / f"{path.stem}.json.gz"
        try:
            with path.open("rb") as src, gzip.open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            path.unlink()
            return True
        except OSError as exc:
            log.error("[archival] no pude archivar %s: %s", path, exc)
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def list_archived(self) -> list[dict[str, Any]]:
        """Para debugging / UI."""
        out = []
        for path in sorted(self.archive_dir.glob("*.json.gz")):
            try:
                stat = path.stat()
                out.append({
                    "name": path.stem.replace(".json", ""),
                    "size_bytes": stat.st_size,
                    "archived_at": stat.st_mtime,
                })
            except OSError:
                continue
        return out

    def restore(self, name: str) -> Optional[Path]:
        """Restaura un archivo desde el archive a sessions_dir."""
        archive = self.archive_dir / f"{name}.json.gz"
        if not archive.exists():
            return None
        target = self.sessions_dir / f"{name}.json"
        try:
            with gzip.open(archive, "rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            archive.unlink()
            return target
        except OSError as exc:
            log.error("[archival] restore falló: %s", exc)
            return None


# ── Singleton ──────────────────────────────────────────────────────────

_instance: Optional[MemoryArchival] = None


def get_memory_archival() -> MemoryArchival:
    global _instance
    if _instance is None:
        _instance = MemoryArchival()
    return _instance
