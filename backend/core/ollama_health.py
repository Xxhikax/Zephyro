"""Lightweight Ollama availability circuit-breaker.

Problem this solves: when Ollama (localhost:11434) is DOWN, every turn fires several
Ollama-dependent calls (memory embeddings, importance/route classifiers) that each retry with
long timeouts on the critical path — measured ~90s of pure overhead per turn while the cloud LLM
itself answers in ~3s. The fix is to probe Ollama ONCE, cache the result with a short TTL, and
trip a breaker on real failures so subsequent calls short-circuit instantly instead of retrying.

Usage:
    from core.ollama_health import is_ollama_up, mark_ollama_down
    if not is_ollama_up():
        return ""          # skip the embedding/classifier call entirely
    try:
        ... real ollama call ...
    except Exception:
        mark_ollama_down()  # trip breaker immediately, don't wait for the next probe
        raise
"""
from __future__ import annotations

import os
import threading
import time

_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
_TTL_UP_S = 30.0        # when up, re-probe at most every 30s
_TTL_DOWN_S = 15.0      # when down, re-probe sooner so recovery is picked up
_PROBE_TIMEOUT_S = 1.0  # a refused/hung Ollama must not cost more than this

_lock = threading.Lock()
_up: "bool | None" = None
_checked_at = 0.0


def _probe() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(f"{_OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def is_ollama_up(force: bool = False) -> bool:
    """Return cached Ollama availability, re-probing only when the TTL expired.

    Cheap to call on the hot path — at most one ~1s probe per TTL window.
    """
    global _up, _checked_at
    now = time.monotonic()
    with _lock:
        cached = _up
        age = now - _checked_at
        ttl = _TTL_UP_S if cached else _TTL_DOWN_S
        if not force and cached is not None and age < ttl:
            return cached
    ok = _probe()
    with _lock:
        _up = ok
        _checked_at = time.monotonic()
    return ok


def mark_ollama_down() -> None:
    """Trip the breaker right after a real Ollama call failed, so we don't pay another
    probe/retry to re-learn it's down."""
    global _up, _checked_at
    with _lock:
        _up = False
        _checked_at = time.monotonic()
