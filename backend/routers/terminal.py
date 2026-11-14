"""Terminal router — audit log + workspace de repos. Admin-only."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth_middleware import require_admin

router = APIRouter()


_TERMINAL_LOG = Path("logs/terminal.log")
_REPOS_INDEX_FILENAME = "_index.json"


@router.get("/terminal/audit")
async def terminal_audit(
    request: Request,
    request_id: str | None = Query(default=None),
    tail: int = Query(default=100, ge=1, le=2000),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """
    Devuelve líneas del terminal.log. Si se pasa `request_id`, filtra solo
    esas líneas. Si no, devuelve las últimas `tail` líneas.
    """
    if not _TERMINAL_LOG.exists():
        return {"lines": [], "log_path": str(_TERMINAL_LOG), "exists": False}

    try:
        if request_id:
            safe = "".join(c for c in request_id if c.isalnum() or c in "-_")[:64]
            with _TERMINAL_LOG.open("r", encoding="utf-8", errors="replace") as f:
                matches = [line.rstrip() for line in f if safe in line]
            return {
                "lines": matches[-tail:],
                "log_path": str(_TERMINAL_LOG),
                "filter": {"request_id": safe},
                "exists": True,
            }

        with _TERMINAL_LOG.open("r", encoding="utf-8", errors="replace") as f:
            recent = deque(f, maxlen=tail)
        return {
            "lines": [l.rstrip() for l in recent],
            "log_path": str(_TERMINAL_LOG),
            "exists": True,
        }
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Error leyendo log: {exc}")


@router.get("/terminal/repos")
async def terminal_repos(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Lista los repos clonados por GitHubSkill desde `_index.json`."""
    repos_root = getattr(request.app.state, "repos_root", None)
    if repos_root is None:
        return {"repos": [], "root": None}

    idx_path = Path(repos_root) / _REPOS_INDEX_FILENAME
    if not idx_path.exists():
        return {"repos": [], "root": str(repos_root)}

    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        return {
            "repos": idx.get("repos", []),
            "root": str(repos_root),
            "updated_at": idx.get("updated_at"),
        }
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Error leyendo índice: {exc}")
