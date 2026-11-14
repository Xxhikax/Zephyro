"""Watcher read-only de CI/CD (GitHub Actions via gh CLI)."""
from .watcher import CIWatcher, get_ci_watcher

__all__ = ["CIWatcher", "get_ci_watcher"]
