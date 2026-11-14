"""Rutas de filesystem canónicas del backend — fuente única de verdad.

Antes `UPLOAD_DIR` estaba redefinido en 5 routers, 4 de ellos como
`Path("storage/uploads")` relativo al CWD (se rompe si el proceso arranca desde
otro directorio). Importar desde acá en su lugar.
"""
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent  # core/ -> backend/
STORAGE_DIR = BACKEND_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
