"""
Persistent session store — saves sessions to disk as JSON.
No Redis dependency. Suitable for single-node local deployment.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict

log = logging.getLogger("zephyro.sessions")
SESSIONS_FILE = Path("storage/sessions.json")


def _serialize(sessions: dict) -> dict:
    """Convert datetime objects to ISO strings for JSON serialization."""
    result = {}
    for token, session in sessions.items():
        s = {}
        for k, v in session.items():
            if isinstance(v, datetime):
                s[k] = v.isoformat()
            else:
                s[k] = v
        result[token] = s
    return result


def _deserialize(data: dict) -> dict:
    """Convert ISO strings back to datetime objects."""
    dt_fields = {"created_at", "last_seen"}
    result = {}
    for token, session in data.items():
        s = {}
        for k, v in session.items():
            if k in dt_fields and isinstance(v, str):
                try:
                    s[k] = datetime.fromisoformat(v)
                except ValueError:
                    s[k] = datetime.now()
            else:
                s[k] = v
        result[token] = s
    return result


async def save_sessions(sessions: dict) -> None:
    """Persist sessions dict to disk."""
    try:
        SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        serialized = _serialize(sessions)
        SESSIONS_FILE.write_text(json.dumps(serialized, indent=2, ensure_ascii=False))
        log.info(f"[session-store] Saved {len(sessions)} sessions to {SESSIONS_FILE}")
    except Exception as exc:
        log.error(f"[session-store] Failed to save sessions: {exc}")


async def load_sessions(session_ttl) -> dict:
    """Load sessions from disk, filtering expired ones."""
    if not SESSIONS_FILE.exists():
        return {}
    try:
        data = json.loads(SESSIONS_FILE.read_text())
        sessions = _deserialize(data)
        now = datetime.now()
        active = {
            token: s for token, s in sessions.items()
            if now - s.get("last_seen", s.get("created_at", now)) <= session_ttl
        }
        expired = len(sessions) - len(active)
        if expired:
            log.info(f"[session-store] Filtered {expired} expired sessions on load")
        log.info(f"[session-store] Loaded {len(active)} active sessions from {SESSIONS_FILE}")
        return active
    except Exception as exc:
        log.error(f"[session-store] Failed to load sessions: {exc}")
        return {}
