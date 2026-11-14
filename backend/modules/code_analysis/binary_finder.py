"""
Localiza binarios externos (gh, opencode, claude) en Windows incluso si no
están en PATH. Buscar en ubicaciones típicas de instalación.

Cache de 30s para no repetir filesystem lookups.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Optional

# Ubicaciones típicas por binario en Windows
_FALLBACK_LOCATIONS: dict[str, tuple[Path, ...]] = {
    "gh": (
        Path(r"C:\Program Files\GitHub CLI\gh.exe"),
        Path(r"C:\Program Files (x86)\GitHub CLI\gh.exe"),
        Path(os.path.expanduser(r"~\scoop\shims\gh.exe")),
        Path(r"C:\ProgramData\chocolatey\bin\gh.exe"),
    ),
    "opencode": (
        Path(os.path.expanduser(r"~\AppData\Roaming\npm\opencode.cmd")),
        Path(os.path.expanduser(r"~\AppData\Roaming\npm\opencode.exe")),
        Path(os.path.expanduser(r"~\.bun\bin\opencode.exe")),
        Path(os.path.expanduser(r"~\scoop\shims\opencode.exe")),
        Path(r"C:\ProgramData\chocolatey\bin\opencode.exe"),
        Path(os.path.expanduser(r"~\AppData\Local\Programs\opencode\opencode.exe")),
    ),
    "claude": (
        Path(os.path.expanduser(r"~\.local\bin\claude.exe")),
        Path(os.path.expanduser(r"~\AppData\Roaming\npm\claude.cmd")),
        Path(os.path.expanduser(r"~\AppData\Roaming\npm\claude.exe")),
    ),
    "npx": (
        Path(r"C:\Program Files\nodejs\npx.cmd"),
        Path(r"C:\Program Files\nodejs\npx"),
    ),
}

_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, tuple[Optional[str], float]] = {}


def find(name: str) -> Optional[str]:
    """
    Localiza el binario `name`. Devuelve la ruta absoluta si existe, None si no.

    Estrategia:
        1. shutil.which(name) — primero PATH
        2. Buscar en _FALLBACK_LOCATIONS[name]
        3. Cachear el resultado por 30s
    """
    cached = _cache.get(name)
    now = time.monotonic()
    if cached and (now - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    # 1. PATH
    found = shutil.which(name)
    if found:
        _cache[name] = (found, now)
        return found

    # 2. Fallbacks
    for candidate in _FALLBACK_LOCATIONS.get(name, ()):
        if candidate.exists():
            result = str(candidate)
            _cache[name] = (result, now)
            return result

    _cache[name] = (None, now)
    return None


def invalidate(name: Optional[str] = None) -> None:
    """Borra cache. Útil tras instalar un binario."""
    if name is None:
        _cache.clear()
    else:
        _cache.pop(name, None)
